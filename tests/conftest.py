"""Pytest-Setup."""

from __future__ import annotations

import json
from pathlib import Path
import sys
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.zoraxy.const import DOMAIN

FIXTURES = Path(__file__).parent / "fixtures"
URL = "http://192.0.2.122:8000"
HOST_UUID = "00000000-1111-2222-3333-444444444444"
ENTRY_DATA = {"url": URL, "username": "admin", "password": "secret", "verify_ssl": True}
LOGIN_PAGE = '<html><head><meta name="zoraxy.csrf.Token" content="csrf-123"></head></html>'


def load(name: str) -> Any:
    return json.loads((FIXTURES / f"{name}.json").read_text())


# (Methode, Pfad) -> Fixture
ENDPOINTS = {
    ("GET", "/api/proxy/status"): "proxy_status",
    ("GET", "/api/stats/overview"): "overview",
    ("GET", "/api/stats/system"): "stats_system",
    ("POST", "/api/proxy/list"): "proxy_list",
    ("GET", "/api/cert/list"): "cert_list",
    ("GET", "/api/acme/listExpiredDomains"): "acme_expired",
    ("GET", "/api/utm/list"): "uptime",
    ("GET", "/api/streamprox/config/list"): "streams",
    ("GET", "/api/redirect/list"): "redirects",
    ("GET", "/api/access/list"): "access_rules",
}


def mock_api(
    aioclient_mock,
    overrides: dict[str, Any] | None = None,
    status: dict[str, int] | None = None,
    login_result: Any = "OK",
) -> None:
    """Registriert Login und alle Lese-Endpunkte; Schlüssel sind Pfade."""
    aioclient_mock.clear_requests()
    overrides = overrides or {}
    status = status or {}
    aioclient_mock.get(f"{URL}/login.html", text=LOGIN_PAGE)
    aioclient_mock.post(f"{URL}/api/auth/login", text=json.dumps(login_result))
    for (method, path), fixture in ENDPOINTS.items():
        register = aioclient_mock.get if method == "GET" else aioclient_mock.post
        if path in status:
            register(f"{URL}{path}", status=status[path])
        else:
            register(f"{URL}{path}", text=json.dumps(overrides.get(path, load(fixture))))


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


@pytest.fixture
def options() -> dict[str, Any]:
    return {"scan_interval": 60, "uptime_interval": 300}


@pytest.fixture
def config_entry(options) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        title="Zoraxy (192.0.2.122)",
        data=ENTRY_DATA,
        unique_id=HOST_UUID,
        options=options,
    )
