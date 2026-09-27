"""Diagnose-Download."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_PASSWORD, CONF_URL, CONF_USERNAME
from homeassistant.core import HomeAssistant

from . import ZoraxyConfigEntry

TO_REDACT = {
    CONF_PASSWORD,
    CONF_USERNAME,
    CONF_URL,
    "BasicAuthCredentials",
    "PasswordHash",
    "Password",
    "BlackListIP",
    "WhiteListIP",
    "ForwardAuthURL",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ZoraxyConfigEntry
) -> dict[str, Any]:
    coordinator = entry.runtime_data.coordinator
    data = coordinator.data
    result: dict[str, Any] = {
        "entry": {
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "options": dict(entry.options),
        },
        "last_update_success": coordinator.last_update_success,
        "unsupported_features": sorted(coordinator.unsupported),
    }
    if data is None:
        return result
    result.update(
        {
            "fetched_at": data.fetched_at.isoformat(),
            "version": data.version,
            "running": data.running,
            "overview": data.overview,
            "system": data.system,
            "hosts": async_redact_data(list(data.hosts.values()), TO_REDACT),
            "certificates": list(data.certificates.values()),
            "expired_domains": data.expired_domains,
            "access_rules": async_redact_data(list(data.access_rules.values()), TO_REDACT),
            "streams": list(data.streams.values()),
            "redirects": list(data.redirects.values()),
            "uptime": {
                key: {**asdict(val), "checked": val.checked.isoformat() if val.checked else None}
                for key, val in data.uptime.items()
            },
        }
    )
    return result
