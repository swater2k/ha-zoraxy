"""Client für die interne API von Zoraxy.

Zoraxy hat keine Token-API. Die Weboberfläche meldet sich per Session-Cookie
an und schickt bei jedem POST den CSRF-Token aus ``login.html`` mit – genau
das macht dieser Client auch. Läuft die Session ab, leitet Zoraxy auf die
Login-Seite um; der Client meldet sich dann einmal neu an und wiederholt.

Nur wenn Zoraxy die Zugangsdaten ausdrücklich ablehnt (Antwort mit
Fehlertext), gilt das als ``ZoraxyAuthError`` und HA fordert eine neue
Anmeldung an. Scheitert der Login an Sitzung oder CSRF-Token (403, 401,
Umleitung), ist das ein vorübergehender Fehler: Cookies verwerfen, einmal neu
versuchen, sonst ``ZoraxyConnectionError``. Zoraxy legt das CSRF-Cookie fest
für 12 Stunden an; danach passt ein gespeicherter Token nicht mehr.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import aiohttp

_LOGGER = logging.getLogger(__name__)

REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=30)
_TOKEN_RE = re.compile(r'name="zoraxy\.csrf\.Token"\s+content="([^"]+)"')
_REDIRECTS = {301, 302, 303, 307, 308}


class ZoraxyError(Exception):
    """Basisfehler."""


class ZoraxyConnectionError(ZoraxyError):
    """Nicht erreichbar oder keine Zoraxy-Antwort."""


class ZoraxyAuthError(ZoraxyError):
    """Anmeldung abgelehnt."""


class ZoraxyRequestError(ZoraxyError):
    """Zoraxy hat die Anfrage mit einer Fehlermeldung beantwortet."""


class ZoraxyNotFoundError(ZoraxyError):
    """Endpunkt fehlt in dieser Zoraxy-Version."""


class _SessionExpired(Exception):
    """Intern: neu anmelden und wiederholen."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def normalize_url(url: str, default_port: int = 8000) -> str:
    """``192.0.2.10`` → ``http://192.0.2.10:8000``; Pfad und Slash entfallen."""
    url = url.strip()
    if "://" not in url:
        url = f"http://{url}"
    parts = urlsplit(url)
    netloc = parts.netloc
    if parts.port is None and parts.scheme == "http" and ":" not in netloc.split("@")[-1]:
        netloc = f"{netloc}:{default_port}"
    return urlunsplit((parts.scheme, netloc, "", "", ""))


class ZoraxyClient:
    """Dünne Hülle um die Endpunkte, die die Integration braucht."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        url: str,
        username: str,
        password: str,
        verify_ssl: bool = True,
    ) -> None:
        self._session = session
        self.url = normalize_url(url)
        self._username = username
        self._password = password
        self._ssl = None if verify_ssl else False
        self._token: str | None = None

    # --- Anmeldung ----------------------------------------------------- #

    async def login(self) -> None:
        """Anmelden; bei Problemen mit Sitzung oder CSRF einmal mit frischen Cookies."""
        try:
            await self._login_once()
        except _SessionExpired as err:
            _LOGGER.warning(
                "Zoraxy-Login nicht angenommen (%s), neuer Versuch mit frischen Cookies",
                err.reason,
            )
            self._session.cookie_jar.clear()
            self._token = None
            try:
                await self._login_once()
            except _SessionExpired as err2:
                self._token = None
                raise ZoraxyConnectionError(
                    f"Login nicht möglich: {err2.reason} (Zugangsdaten nicht abgelehnt)"
                ) from err2

    async def _login_once(self) -> None:
        try:
            async with self._session.get(
                f"{self.url}/login.html", ssl=self._ssl, timeout=REQUEST_TIMEOUT
            ) as resp:
                page = await resp.text()
        except (aiohttp.ClientError, TimeoutError) as err:
            raise ZoraxyConnectionError(f"login.html: {err}") from err
        match = _TOKEN_RE.search(page)
        if match is None:
            raise ZoraxyConnectionError("Keine Zoraxy-Anmeldeseite (CSRF-Token fehlt)")
        self._token = match.group(1)
        try:
            result = await self._send(
                "POST",
                "/api/auth/login",
                {"username": self._username, "password": self._password},
                None,
            )
        except ZoraxyRequestError as err:
            self._token = None
            raise ZoraxyAuthError(str(err)) from err
        if result != "OK":
            self._token = None
            raise ZoraxyAuthError(f"Unerwartete Antwort beim Login: {result!r}")

    async def _send(
        self,
        method: str,
        path: str,
        data: dict[str, Any] | None,
        params: dict[str, Any] | None,
    ) -> Any:
        headers = {"Accept": "application/json", "Referer": f"{self.url}/"}
        if method != "GET" and self._token:
            headers["X-CSRF-Token"] = self._token
        try:
            async with self._session.request(
                method,
                f"{self.url}{path}",
                data=data,
                params=params,
                headers=headers,
                ssl=self._ssl,
                timeout=REQUEST_TIMEOUT,
                allow_redirects=False,
            ) as resp:
                body = await resp.text()
                status = resp.status
        except (aiohttp.ClientError, TimeoutError) as err:
            raise ZoraxyConnectionError(f"{method} {path}: {err}") from err

        if status in _REDIRECTS or status == 401:
            raise _SessionExpired(f"{method} {path}: HTTP {status}")
        if status == 403:
            # Ungültiger CSRF-Token – nach Ablauf des CSRF-Cookies (12 h)
            # oder einem Neustart von Zoraxy.
            raise _SessionExpired(f"{method} {path}: HTTP 403")
        if status == 404:
            raise ZoraxyNotFoundError(f"{path} nicht gefunden")
        if status >= 400:
            raise ZoraxyRequestError(f"{method} {path}: HTTP {status}")
        text = body.strip()
        if not text:
            return None
        if text.startswith("<"):
            # HTML statt JSON: Zoraxy hat die Login-Seite ausgeliefert.
            raise _SessionExpired(f"{method} {path}: Login-Seite statt JSON")
        try:
            result = json.loads(text)
        except ValueError as err:
            raise ZoraxyConnectionError(f"{path}: keine JSON-Antwort") from err
        if isinstance(result, dict) and set(result) == {"error"}:
            raise ZoraxyRequestError(str(result["error"]))
        return result

    async def _request(
        self,
        method: str,
        path: str,
        data: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> Any:
        if self._token is None:
            await self.login()
        try:
            return await self._send(method, path, data, params)
        except _SessionExpired:
            await self.login()
        try:
            return await self._send(method, path, data, params)
        except _SessionExpired as err:
            # Der Login hat geklappt, also stimmen die Zugangsdaten: kein Reauth.
            raise ZoraxyConnectionError(
                f"{path}: Sitzung wird nach Neuanmeldung nicht akzeptiert ({err.reason})"
            ) from err

    # --- Lesen ------------------------------------------------------------ #

    async def status(self) -> dict[str, Any]:
        return await self._request("GET", "/api/proxy/status")

    async def overview(self) -> dict[str, Any]:
        return await self._request("GET", "/api/stats/overview")

    async def summary(self) -> dict[str, Any]:
        return await self._request("GET", "/api/stats/summary", params={"fast": "true"})

    async def system(self) -> dict[str, Any]:
        return await self._request("GET", "/api/stats/system")

    async def hosts(self) -> list[dict[str, Any]]:
        return await self._request("POST", "/api/proxy/list", {"type": "host"}) or []

    async def certificates(self) -> list[dict[str, Any]]:
        return await self._request("GET", "/api/cert/list", params={"date": "true"}) or []

    async def expired_domains(self) -> list[str]:
        result = await self._request("GET", "/api/acme/listExpiredDomains") or {}
        return list(result.get("domain") or [])

    async def uptime(self) -> dict[str, list[dict[str, Any]]]:
        return await self._request("GET", "/api/utm/list") or {}

    async def streams(self) -> list[dict[str, Any]]:
        return await self._request("GET", "/api/streamprox/config/list") or []

    async def redirects(self) -> list[dict[str, Any]]:
        return await self._request("GET", "/api/redirect/list") or []

    async def access_rules(self) -> list[dict[str, Any]]:
        return await self._request("GET", "/api/access/list") or []

    # --- Schreiben ---------------------------------------------------------- #

    async def set_host_enabled(self, domain: str, enabled: bool) -> None:
        await self._request("POST", "/api/proxy/toggle", {"ep": domain, "enable": _bool(enabled)})

    async def set_proxy_running(self, running: bool) -> None:
        await self._request("POST", "/api/proxy/enable", {"enable": _bool(running)})

    async def set_blacklist_enabled(self, rule_id: str, enabled: bool) -> None:
        await self._request(
            "POST", "/api/blacklist/enable", {"id": rule_id, "enable": _bool(enabled)}
        )

    async def set_whitelist_enabled(self, rule_id: str, enabled: bool) -> None:
        await self._request(
            "POST", "/api/whitelist/enable", {"id": rule_id, "enable": _bool(enabled)}
        )

    async def blacklist_ip(self, rule_id: str, ip: str, comment: str = "") -> None:
        # Zoraxy liest den Kommentar aus der Query, id und ip aus dem Formular.
        await self._request(
            "POST",
            "/api/blacklist/ip/add",
            {"id": rule_id, "ip": ip},
            params={"comment": comment} if comment else None,
        )

    async def unblacklist_ip(self, rule_id: str, ip: str) -> None:
        await self._request("POST", "/api/blacklist/ip/remove", {"id": rule_id, "ip": ip})

    async def set_stream_running(self, uuid: str, running: bool) -> None:
        path = "/api/streamprox/config/start" if running else "/api/streamprox/config/stop"
        await self._request("POST", path, {"uuid": uuid})

    async def set_redirect_enabled(self, redirect_url: str, enabled: bool) -> None:
        await self._request(
            "POST",
            "/api/redirect/toggle",
            {"redirectUrl": redirect_url, "enabled": _bool(enabled)},
        )

    async def renew_certificates(self) -> None:
        await self._request("POST", "/api/acme/autoRenew/renewNow")


def _bool(value: bool) -> str:
    return "true" if value else "false"
