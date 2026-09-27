"""DataUpdateCoordinator für Zoraxy."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
import logging
import time
from typing import Any, TypeVar

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import ZoraxyAuthError, ZoraxyClient, ZoraxyError, ZoraxyNotFoundError
from .const import (
    CONF_UPTIME_INTERVAL,
    DEFAULT_UPTIME_INTERVAL,
    DOMAIN,
    STATUS_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)

T = TypeVar("T")


def parse_zoraxy_time(value: Any) -> datetime | None:
    """Zoraxy liefert ``2026-11-05 11:53:26`` ohne Zeitzone – das ist UTC."""
    if not value or not isinstance(value, str):
        return None
    parsed = dt_util.parse_datetime(value.replace(" ", "T", 1))
    if parsed is None:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


@dataclass(slots=True)
class UptimeData:
    """Letzte Prüfung und Kennzahlen über den gelieferten Verlauf."""

    online: bool | None
    latency: int | None
    status_code: int | None
    checked: datetime | None
    availability: float | None
    url: str | None


@dataclass(slots=True)
class ZoraxyData:
    hosts: dict[str, dict[str, Any]]
    certificates: dict[str, dict[str, Any]]
    access_rules: dict[str, dict[str, Any]]
    streams: dict[str, dict[str, Any]]
    redirects: dict[str, dict[str, Any]]
    expired_domains: list[str]
    overview: dict[str, Any] | None
    system: dict[str, Any] | None
    uptime: dict[str, UptimeData]
    version: str | None
    host_uuid: str | None
    status_running: bool | None
    fetched_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def running(self) -> bool | None:
        if self.overview is not None and "ProxyRunning" in self.overview:
            return bool(self.overview["ProxyRunning"])
        return self.status_running


def summarize_uptime(entries: list[dict[str, Any]]) -> UptimeData:
    if not entries:
        return UptimeData(None, None, None, None, None, None)
    entries = sorted(entries, key=lambda e: e.get("Timestamp") or 0)
    last = entries[-1]
    online_count = sum(1 for e in entries if e.get("Online"))
    checked = None
    if isinstance(last.get("Timestamp"), int | float):
        checked = datetime.fromtimestamp(last["Timestamp"], tz=UTC)
    return UptimeData(
        online=bool(last.get("Online")),
        latency=last.get("Latency"),
        status_code=last.get("StatusCode"),
        checked=checked,
        availability=round(online_count / len(entries) * 100, 1),
        url=last.get("URL"),
    )


class ZoraxyCoordinator(DataUpdateCoordinator[ZoraxyData]):
    """Liest Zoraxy; teure Endpunkte werden seltener abgefragt."""

    config_entry: ConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: ZoraxyClient,
        interval: int,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(seconds=interval),
        )
        self.client = client
        self.unsupported: set[str] = set()
        self._uptime: dict[str, UptimeData] = {}
        self._uptime_at: float | None = None
        self._status: dict[str, Any] = {}
        self._status_at: float | None = None

    async def _optional(self, feature: str, call: Awaitable[T]) -> T | None:
        if feature in self.unsupported:
            if asyncio.iscoroutine(call):
                call.close()
            return None
        try:
            return await call
        except ZoraxyNotFoundError:
            _LOGGER.info("Zoraxy kennt %s nicht, Bereich wird ausgelassen", feature)
            self.unsupported.add(feature)
        return None

    def _due(self, last: float | None, interval: float) -> bool:
        return last is None or time.monotonic() - last >= interval

    def force_uptime_refresh(self) -> None:
        self._uptime_at = None

    async def _async_update_data(self) -> ZoraxyData:
        uptime_interval = self.config_entry.options.get(
            CONF_UPTIME_INTERVAL, DEFAULT_UPTIME_INTERVAL
        )
        try:
            hosts, certificates, access_rules = await asyncio.gather(
                self.client.hosts(),
                self.client.certificates(),
                self.client.access_rules(),
            )
            overview, system, streams, redirects, expired = await asyncio.gather(
                self._optional("overview", self.client.overview()),
                self._optional("system", self.client.system()),
                self._optional("streams", self.client.streams()),
                self._optional("redirects", self.client.redirects()),
                self._optional("expired", self.client.expired_domains()),
            )
            if overview is None:
                # Ältere Zoraxy-Versionen ohne Übersicht: nur die Tageszähler.
                summary = await self._optional("summary", self.client.summary())
                if summary is not None:
                    overview = {
                        "TotalRequestToday": summary.get("TotalRequest"),
                        "ErrorRequestToday": summary.get("ErrorRequest"),
                        "ValidRequestToday": summary.get("ValidRequest"),
                    }
            if self._due(self._status_at, STATUS_INTERVAL):
                self._status = await self.client.status() or {}
                self._status_at = time.monotonic()
            if self._due(self._uptime_at, uptime_interval):
                raw = await self._optional("uptime", self.client.uptime()) or {}
                self._uptime = {key: summarize_uptime(val or []) for key, val in raw.items()}
                self._uptime_at = time.monotonic()
        except ZoraxyAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except ZoraxyError as err:
            raise UpdateFailed(str(err)) from err

        option = self._status.get("Option") or {}
        return ZoraxyData(
            hosts={
                h["RootOrMatchingDomain"]: h
                for h in hosts
                if isinstance(h, dict) and h.get("RootOrMatchingDomain")
            },
            certificates={c["Domain"]: c for c in certificates if c.get("Domain")},
            access_rules={r["ID"]: r for r in access_rules if r.get("ID")},
            streams={s["UUID"]: s for s in streams or [] if s.get("UUID")},
            redirects={r["RedirectURL"]: r for r in redirects or [] if r.get("RedirectURL")},
            expired_domains=expired or [],
            overview=overview,
            system=system,
            uptime=self._uptime,
            version=option.get("HostVersion"),
            host_uuid=option.get("HostUUID"),
            status_running=self._status.get("Running"),
        )
