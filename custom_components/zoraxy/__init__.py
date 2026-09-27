"""Zoraxy-Integration für Home Assistant."""

from __future__ import annotations

from dataclasses import dataclass

import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_PASSWORD,
    CONF_URL,
    CONF_USERNAME,
    CONF_VERIFY_SSL,
    Platform,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv, device_registry as dr
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from homeassistant.helpers.typing import ConfigType

from .api import ZoraxyClient
from .const import CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL, DOMAIN
from .coordinator import ZoraxyCoordinator
from .services import async_setup_services

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.SENSOR,
    Platform.SWITCH,
]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


@dataclass(slots=True)
class ZoraxyRuntimeData:
    coordinator: ZoraxyCoordinator
    server_device_id: str
    session: aiohttp.ClientSession


type ZoraxyConfigEntry = ConfigEntry[ZoraxyRuntimeData]


def build_client(
    hass: HomeAssistant, data: dict, auto_cleanup: bool = True
) -> tuple[ZoraxyClient, aiohttp.ClientSession]:
    """Eigene Session je Eintrag: Zoraxy arbeitet mit Session-Cookies.

    ``unsafe=True`` erlaubt Cookies auch für IP-Adressen, sonst verwirft
    aiohttp das Session-Cookie bei ``http://192.0.2.10:8000``. Mit
    ``auto_cleanup`` trennt HA die Session beim Entladen des Eintrags selbst;
    schließen darf die Integration sie nicht.
    """
    verify_ssl = data.get(CONF_VERIFY_SSL, True)
    session = async_create_clientsession(
        hass,
        verify_ssl=verify_ssl,
        auto_cleanup=auto_cleanup,
        cookie_jar=aiohttp.CookieJar(unsafe=True),
    )
    client = ZoraxyClient(
        session, data[CONF_URL], data[CONF_USERNAME], data[CONF_PASSWORD], verify_ssl
    )
    return client, session


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    async_setup_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ZoraxyConfigEntry) -> bool:
    client, session = build_client(hass, dict(entry.data))
    coordinator = ZoraxyCoordinator(
        hass, entry, client, entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
    )
    await coordinator.async_config_entry_first_refresh()

    # Das Server-Gerät muss existieren, bevor Proxy-Hosts darauf verweisen.
    server = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, entry.entry_id)},
        name="Zoraxy",
        manufacturer="Zoraxy",
        model="Reverse proxy",
        sw_version=coordinator.data.version,
        entry_type=dr.DeviceEntryType.SERVICE,
        configuration_url=client.url,
    )
    entry.runtime_data = ZoraxyRuntimeData(coordinator, server.id, session)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ZoraxyConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_config_entry_device(
    hass: HomeAssistant, entry: ZoraxyConfigEntry, device
) -> bool:
    """Proxy-Host-Geräte dürfen entfernt werden, sobald Zoraxy sie nicht mehr kennt."""
    data = entry.runtime_data.coordinator.data
    prefix = f"{entry.entry_id}_host_"
    for _, identifier in device.identifiers:
        if identifier == entry.entry_id:
            return False
        if identifier.startswith(prefix) and identifier[len(prefix) :] in data.hosts:
            return False
    return True


def option_enabled(entry: ZoraxyConfigEntry, option: str) -> bool:
    return bool(entry.options.get(option, False))
