"""Setup, Entitäten, Session-Handling, Steuerung und Config-Flow."""

from __future__ import annotations

import json

from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import device_registry as dr, entity_registry as er
import pytest
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
    AiohttpClientMockResponse,
)

from custom_components.zoraxy.api import normalize_url
from custom_components.zoraxy.const import DOMAIN
from custom_components.zoraxy.entity import find_device, host_device_id

from .conftest import ENTRY_DATA, HOST_UUID, LOGIN_PAGE, URL, load, mock_api


async def _setup(hass: HomeAssistant, entry) -> None:
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def _refresh(hass: HomeAssistant, entry) -> None:
    await entry.runtime_data.coordinator.async_refresh()
    await hass.async_block_till_done()


def _calls(aioclient_mock: AiohttpClientMocker, path: str) -> list:
    return [c for c in aioclient_mock.mock_calls if c[1].path == path]


# --------------------------------------------------------------------------- #
# Entitäten                                                                    #
# --------------------------------------------------------------------------- #


async def test_entities(hass: HomeAssistant, config_entry, aioclient_mock) -> None:
    mock_api(aioclient_mock)
    await _setup(hass, config_entry)
    assert config_entry.state is ConfigEntryState.LOADED

    # Server
    assert hass.states.get("binary_sensor.zoraxy_proxy_running").state == "on"
    assert hass.states.get("sensor.zoraxy_requests_today").state == "1234"
    assert hass.states.get("sensor.zoraxy_errors_today").state == "12"
    assert hass.states.get("sensor.zoraxy_unique_visitors_today").state == "5"
    assert hass.states.get("sensor.zoraxy_active_connections").state == "7"
    assert hass.states.get("sensor.zoraxy_proxy_hosts").state == "4"
    assert hass.states.get("sensor.zoraxy_version").state == "3.3.5"
    assert hass.states.get("sensor.zoraxy_expired_certificates").state == "0"
    received = hass.states.get("sensor.zoraxy_received_today")
    assert float(received.state) == pytest.approx(52.4, abs=0.1)  # MB
    assert hass.states.get("sensor.zoraxy_memory_usage").state == "6.4"

    cert = hass.states.get("sensor.zoraxy_certificate_example_com")
    assert cert.state == "2026-11-05T11:53:26+00:00"
    assert cert.attributes["remaining_days"] == 38
    assert hass.states.get("binary_sensor.zoraxy_blacklist_default").state == "off"

    # Proxy-Host mit Uptime-Daten
    assert hass.states.get("binary_sensor.adguard_example_net_upstream_online").state == "on"
    assert hass.states.get("binary_sensor.adguard_example_net_enabled").state == "on"
    assert hass.states.get("sensor.adguard_example_net_upstream").state == "192.0.2.84:443"
    assert hass.states.get("sensor.adguard_example_net_availability").state == "100.0"
    assert hass.states.get("sensor.pbs_example_net_availability").state == "91.7"

    # Deaktivierter Host: kein Monitor, daher nicht verfügbar statt „offline“
    assert hass.states.get("binary_sensor.auth_example_com_enabled").state == "off"
    assert hass.states.get("binary_sensor.auth_example_com_upstream_online").state == "unavailable"

    # ohne Optionen keine Steuerung
    assert not hass.states.async_entity_ids("switch")
    assert not hass.states.async_entity_ids("button")


async def test_login_uses_csrf_token(hass: HomeAssistant, config_entry, aioclient_mock) -> None:
    mock_api(aioclient_mock)
    await _setup(hass, config_entry)
    ((_, _, data, headers),) = _calls(aioclient_mock, "/api/auth/login")
    assert data == {"username": "admin", "password": "secret"}
    assert headers["X-CSRF-Token"] == "csrf-123"
    # GETs brauchen keinen Token, POSTs schon
    ((_, _, _, list_headers),) = _calls(aioclient_mock, "/api/proxy/list")[:1]
    assert list_headers["X-CSRF-Token"] == "csrf-123"


async def test_uptime_polled_less_often(hass: HomeAssistant, config_entry, aioclient_mock) -> None:
    mock_api(aioclient_mock)
    await _setup(hass, config_entry)
    assert len(_calls(aioclient_mock, "/api/utm/list")) == 1
    assert len(_calls(aioclient_mock, "/api/proxy/status")) == 1
    await _refresh(hass, config_entry)
    await _refresh(hass, config_entry)
    assert len(_calls(aioclient_mock, "/api/utm/list")) == 1
    assert len(_calls(aioclient_mock, "/api/proxy/status")) == 1
    assert len(_calls(aioclient_mock, "/api/proxy/list")) == 3


async def test_session_expiry_relogin(hass: HomeAssistant, config_entry, aioclient_mock) -> None:
    mock_api(aioclient_mock)
    await _setup(hass, config_entry)

    # Nächster Abruf: erste Antwort ist die Umleitung auf die Login-Seite.
    mock_api(aioclient_mock)
    answers = iter([307, 200])

    async def _overview(method, url, data):
        status = next(answers, 200)
        if status == 307:
            return AiohttpClientMockResponse(method, url, status=307, text=LOGIN_PAGE)
        return AiohttpClientMockResponse(method, url, text=json.dumps(load("overview")))

    aioclient_mock._mocks = [
        m for m in aioclient_mock._mocks if not str(m._url).endswith("/api/stats/overview")
    ]
    aioclient_mock.get(f"{URL}/api/stats/overview", side_effect=_overview)
    await _refresh(hass, config_entry)

    assert config_entry.runtime_data.coordinator.last_update_success
    assert len(_calls(aioclient_mock, "/api/auth/login")) == 1
    assert hass.states.get("sensor.zoraxy_requests_today").state == "1234"


def _replace(aioclient_mock: AiohttpClientMocker, method: str, path: str, side_effect) -> None:
    """Ersetzt die Antwort eines Endpunkts durch eine Funktion."""
    aioclient_mock._mocks = [
        m
        for m in aioclient_mock._mocks
        if not (m.method == method.lower() and str(m._url).endswith(path))
    ]
    register = aioclient_mock.get if method == "GET" else aioclient_mock.post
    register(f"{URL}{path}", side_effect=side_effect)


def _login_answers(statuses):
    """Login-Antworten nacheinander: Statuscode oder "OK"."""
    answers = iter(statuses)

    async def _login(method, url, data):
        answer = next(answers, "OK")
        if answer == "OK":
            return AiohttpClientMockResponse(method, url, text=json.dumps("OK"))
        return AiohttpClientMockResponse(method, url, status=answer, text="Forbidden")

    return _login


def _overview_answers(statuses):
    answers = iter(statuses)

    async def _overview(method, url, data):
        status = next(answers, 200)
        if status != 200:
            return AiohttpClientMockResponse(method, url, status=status, text=LOGIN_PAGE)
        return AiohttpClientMockResponse(method, url, text=json.dumps(load("overview")))

    return _overview


async def test_relogin_csrf_rejected_retries_with_fresh_cookies(
    hass: HomeAssistant, config_entry, aioclient_mock, caplog
) -> None:
    """Abgelaufenes CSRF-Cookie: erster Login 403, zweiter mit frischen Cookies klappt."""
    mock_api(aioclient_mock)
    await _setup(hass, config_entry)

    mock_api(aioclient_mock)
    _replace(aioclient_mock, "GET", "/api/stats/overview", _overview_answers([307]))
    _replace(aioclient_mock, "POST", "/api/auth/login", _login_answers([403]))
    await _refresh(hass, config_entry)

    assert config_entry.runtime_data.coordinator.last_update_success
    assert len(_calls(aioclient_mock, "/api/auth/login")) == 2
    assert len(_calls(aioclient_mock, "/login.html")) == 2
    assert "HTTP 403" in caplog.text
    assert not [
        f for f in hass.config_entries.flow.async_progress() if f["context"]["source"] == "reauth"
    ]


async def test_relogin_csrf_rejected_twice_is_temporary(
    hass: HomeAssistant, config_entry, aioclient_mock, caplog
) -> None:
    """Login scheitert dauerhaft an CSRF: Abruf schlägt fehl, aber kein Reauth."""
    mock_api(aioclient_mock)
    await _setup(hass, config_entry)

    mock_api(aioclient_mock)
    _replace(aioclient_mock, "GET", "/api/stats/overview", _overview_answers([307]))
    _replace(aioclient_mock, "POST", "/api/auth/login", _login_answers([403, 403, 403]))
    await _refresh(hass, config_entry)

    assert not config_entry.runtime_data.coordinator.last_update_success
    assert config_entry.state is ConfigEntryState.LOADED
    assert not [
        f for f in hass.config_entries.flow.async_progress() if f["context"]["source"] == "reauth"
    ]
    assert "Zugangsdaten nicht abgelehnt" in caplog.text

    # Beim nächsten Abruf klappt es wieder, ohne Zutun.
    mock_api(aioclient_mock)
    await _refresh(hass, config_entry)
    assert config_entry.runtime_data.coordinator.last_update_success
    assert hass.states.get("sensor.zoraxy_requests_today").state == "1234"


async def test_session_rejected_after_login_is_temporary(
    hass: HomeAssistant, config_entry, aioclient_mock
) -> None:
    """Login klappt, die Anfrage wird trotzdem abgewiesen: kein Reauth."""
    mock_api(aioclient_mock)
    await _setup(hass, config_entry)

    mock_api(aioclient_mock)
    _replace(aioclient_mock, "GET", "/api/stats/overview", _overview_answers([307, 307, 307]))
    await _refresh(hass, config_entry)

    assert not config_entry.runtime_data.coordinator.last_update_success
    assert not [
        f for f in hass.config_entries.flow.async_progress() if f["context"]["source"] == "reauth"
    ]


async def test_login_rejected_starts_reauth(
    hass: HomeAssistant, config_entry, aioclient_mock
) -> None:
    mock_api(aioclient_mock, login_result={"error": "Invalid username or password"})
    config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert config_entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress()
    assert any(f["context"]["source"] == "reauth" for f in flows)


async def test_older_zoraxy_without_overview(
    hass: HomeAssistant, config_entry, aioclient_mock
) -> None:
    mock_api(aioclient_mock, status={"/api/stats/overview": 404})
    aioclient_mock.get(
        f"{URL}/api/stats/summary",
        text=json.dumps({"TotalRequest": 99, "ErrorRequest": 1, "ValidRequest": 98}),
    )
    await _setup(hass, config_entry)
    assert hass.states.get("sensor.zoraxy_requests_today").state == "99"
    assert hass.states.get("sensor.zoraxy_active_connections") is None


async def test_removed_host_removes_device(
    hass: HomeAssistant, config_entry, aioclient_mock
) -> None:
    mock_api(aioclient_mock)
    await _setup(hass, config_entry)
    registry = dr.async_get(hass)
    ident = host_device_id(config_entry.entry_id, "pbs.example.net")
    assert find_device(registry, ident, config_entry.entry_id) is not None

    hosts = [h for h in load("proxy_list") if h["RootOrMatchingDomain"] != "pbs.example.net"]
    mock_api(aioclient_mock, {"/api/proxy/list": hosts})
    await _refresh(hass, config_entry)
    assert find_device(registry, ident, config_entry.entry_id) is None
    assert hass.states.get("sensor.pbs_example_net_availability") is None


# --------------------------------------------------------------------------- #
# Steuerung                                                                    #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("options", [{"control_hosts": True, "control_proxy": True}])
async def test_host_and_proxy_switches(hass: HomeAssistant, config_entry, aioclient_mock) -> None:
    mock_api(aioclient_mock)
    await _setup(hass, config_entry)
    host = "switch.adguard_example_net_enabled"
    assert hass.states.get(host).state == "on"
    assert hass.states.get("switch.zoraxy_reverse_proxy").state == "on"
    # Mit Schalter entfällt der Binärsensor für „Aktiv“.
    assert hass.states.get("binary_sensor.adguard_example_net_enabled") is None

    mock_api(aioclient_mock)
    aioclient_mock.post(f"{URL}/api/proxy/toggle", text='"OK"')
    aioclient_mock.post(f"{URL}/api/proxy/enable", text='"OK"')
    await hass.services.async_call("switch", "turn_off", {"entity_id": host}, blocking=True)
    await hass.services.async_call(
        "switch", "turn_off", {"entity_id": "switch.zoraxy_reverse_proxy"}, blocking=True
    )

    ((_, _, data, headers),) = _calls(aioclient_mock, "/api/proxy/toggle")
    assert data == {"ep": "adguard.example.net", "enable": "false"}
    assert headers["X-CSRF-Token"] == "csrf-123"
    ((_, _, data, _),) = _calls(aioclient_mock, "/api/proxy/enable")
    assert data == {"enable": "false"}


@pytest.mark.parametrize("options", [{"control_access": True}])
async def test_access_control(hass: HomeAssistant, config_entry, aioclient_mock) -> None:
    mock_api(aioclient_mock)
    await _setup(hass, config_entry)
    assert hass.states.get("switch.zoraxy_blacklist_default").state == "off"

    mock_api(aioclient_mock)
    aioclient_mock.post(f"{URL}/api/blacklist/enable", text='"OK"')
    aioclient_mock.post(f"{URL}/api/blacklist/ip/add", text='"OK"')
    aioclient_mock.post(f"{URL}/api/blacklist/ip/remove", text='"OK"')
    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": "switch.zoraxy_blacklist_default"}, blocking=True
    )
    await hass.services.async_call(
        DOMAIN, "block_ip", {"ip": "203.0.113.7", "comment": "test"}, blocking=True
    )
    await hass.services.async_call(DOMAIN, "unblock_ip", {"ip": "203.0.113.7"}, blocking=True)

    ((_, _, data, _),) = _calls(aioclient_mock, "/api/blacklist/enable")
    assert data == {"id": "default", "enable": "true"}
    ((_, url, data, _),) = _calls(aioclient_mock, "/api/blacklist/ip/add")
    assert data == {"id": "default", "ip": "203.0.113.7"}
    assert url.query["comment"] == "test"
    ((_, _, data, _),) = _calls(aioclient_mock, "/api/blacklist/ip/remove")
    assert data == {"id": "default", "ip": "203.0.113.7"}


async def test_block_ip_needs_option(hass: HomeAssistant, config_entry, aioclient_mock) -> None:
    mock_api(aioclient_mock)
    await _setup(hass, config_entry)
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(DOMAIN, "block_ip", {"ip": "203.0.113.7"}, blocking=True)


@pytest.mark.parametrize("options", [{"control_certificates": True}])
async def test_renew_button(hass: HomeAssistant, config_entry, aioclient_mock) -> None:
    mock_api(aioclient_mock)
    await _setup(hass, config_entry)
    mock_api(aioclient_mock)
    aioclient_mock.post(f"{URL}/api/acme/autoRenew/renewNow", text='"OK"')
    await hass.services.async_call(
        "button", "press", {"entity_id": "button.zoraxy_renew_certificates"}, blocking=True
    )
    assert len(_calls(aioclient_mock, "/api/acme/autoRenew/renewNow")) == 1


async def test_unload(hass: HomeAssistant, config_entry, aioclient_mock) -> None:
    mock_api(aioclient_mock)
    await _setup(hass, config_entry)
    assert await hass.config_entries.async_unload(config_entry.entry_id)
    assert config_entry.state is ConfigEntryState.NOT_LOADED


# --------------------------------------------------------------------------- #
# Config-Flow                                                                  #
# --------------------------------------------------------------------------- #


async def test_user_flow(hass: HomeAssistant, aioclient_mock) -> None:
    mock_api(aioclient_mock)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"url": "192.0.2.122", "username": " admin ", "password": "secret", "verify_ssl": True},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Zoraxy (192.0.2.122)"
    assert result["data"] == ENTRY_DATA
    assert result["result"].unique_id == HOST_UUID


@pytest.mark.parametrize(
    ("login_page", "login_result", "error"),
    [
        (LOGIN_PAGE, {"error": "Invalid username or password"}, "invalid_auth"),
        ("<html>no zoraxy here</html>", "OK", "cannot_connect"),
    ],
)
async def test_user_flow_errors(
    hass: HomeAssistant, aioclient_mock, login_page, login_result, error
) -> None:
    mock_api(aioclient_mock, login_result=login_result)
    aioclient_mock._mocks = [
        m for m in aioclient_mock._mocks if not str(m._url).endswith("login.html")
    ]
    aioclient_mock.get(f"{URL}/login.html", text=login_page)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], ENTRY_DATA)
    assert result["errors"] == {"base": error}


async def test_reauth(hass: HomeAssistant, config_entry, aioclient_mock) -> None:
    config_entry.add_to_hass(hass)
    mock_api(aioclient_mock)
    result = await config_entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"username": "admin", "password": "new"}
    )
    assert result["reason"] == "reauth_successful"
    assert config_entry.data["password"] == "new"


async def test_options_enable_control(hass: HomeAssistant, config_entry, aioclient_mock) -> None:
    mock_api(aioclient_mock)
    await _setup(hass, config_entry)
    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"scan_interval": 60, "uptime_interval": 300, "control_hosts": True}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert hass.states.get("switch.adguard_example_net_enabled") is not None


def test_normalize_url() -> None:
    assert normalize_url("192.0.2.1") == "http://192.0.2.1:8000"
    assert normalize_url("http://192.0.2.1:9000/") == "http://192.0.2.1:9000"
    assert normalize_url("https://zoraxy.example.net/login.html") == "https://zoraxy.example.net"


async def test_option_change_removes_replaced_entities(
    hass: HomeAssistant, config_entry, aioclient_mock
) -> None:
    """Steuerung an: Binärsensor „Aktiv“ weicht dem Schalter – und umgekehrt."""
    mock_api(aioclient_mock)
    await _setup(hass, config_entry)
    registry = er.async_get(hass)
    assert registry.async_get("binary_sensor.adguard_example_net_enabled")

    hass.config_entries.async_update_entry(config_entry, options={"control_hosts": True})
    await hass.config_entries.async_reload(config_entry.entry_id)
    await hass.async_block_till_done()
    assert registry.async_get("binary_sensor.adguard_example_net_enabled") is None
    assert registry.async_get("switch.adguard_example_net_enabled")

    hass.config_entries.async_update_entry(config_entry, options={"control_hosts": False})
    await hass.config_entries.async_reload(config_entry.entry_id)
    await hass.async_block_till_done()
    assert registry.async_get("switch.adguard_example_net_enabled") is None
    assert registry.async_get("binary_sensor.adguard_example_net_enabled")


async def test_entity_ids_ignore_area(hass: HomeAssistant, config_entry, aioclient_mock) -> None:
    """Neue Entitäten bekommen kein Bereichs-Präfix, auch wenn das Gerät einen Bereich hat."""
    from homeassistant.helpers import area_registry as ar

    mock_api(aioclient_mock)
    await _setup(hass, config_entry)
    area = ar.async_get(hass).async_create("Waschraum")
    devices = dr.async_get(hass)
    server = find_device(devices, config_entry.entry_id, config_entry.entry_id)
    devices.async_update_device(server.id, area_id=area.id)

    certs = load("cert_list")
    certs.append(dict(certs[0], Domain="*.example.org", Filename="example.org"))
    hosts = load("proxy_list")
    hosts.append(dict(hosts[0], RootOrMatchingDomain="new.example.net"))
    mock_api(aioclient_mock, {"/api/cert/list": certs, "/api/proxy/list": hosts})
    await _refresh(hass, config_entry)

    assert hass.states.get("sensor.zoraxy_certificate_example_org") is not None
    assert hass.states.get("sensor.new_example_net_latency") is not None
    assert not [e for e in hass.states.async_entity_ids() if "waschraum" in e]
