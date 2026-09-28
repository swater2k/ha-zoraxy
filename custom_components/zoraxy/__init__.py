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
from homeassistant.helpers import (
    config_validation as cv,
    device_registry as dr,
    entity_registry as er,
)
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from homeassistant.helpers.typing import ConfigType

from .api import ZoraxyClient
from .const import (
    CONF_CONTROL_ACCESS,
    CONF_CONTROL_CERTIFICATES,
    CONF_CONTROL_HOSTS,
    CONF_CONTROL_PROXY,
    CONF_CONTROL_REDIRECTS,
    CONF_CONTROL_STREAMS,
    CONF_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
)
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
    _remove_replaced_entities(hass, entry)
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


def _replaced_by_option(entity: er.RegistryEntry, entry_id: str) -> str | None:
    """Welche Option über diese Entität entscheidet – und in welche Richtung.

    Liefert ``"+option"``, wenn die Entität nur bei aktiver Option existiert
    (Schalter, Button), und ``"-option"``, wenn sie dann durch einen Schalter
    ersetzt wird (Binärsensor). ``None`` für alle übrigen Entitäten.
    """
    uid = entity.unique_id.removeprefix(f"{entry_id}_")
    if entity.domain == "button":
        return f"+{CONF_CONTROL_CERTIFICATES}"
    if entity.domain == "switch":
        for prefix, option in (
            ("proxy_switch", CONF_CONTROL_PROXY),
            ("host_", CONF_CONTROL_HOSTS),
            ("access_", CONF_CONTROL_ACCESS),
            ("stream_", CONF_CONTROL_STREAMS),
            ("redirect_", CONF_CONTROL_REDIRECTS),
        ):
            if uid.startswith(prefix):
                return f"+{option}"
    if entity.domain == "binary_sensor":
        if uid.startswith("host_") and uid.endswith("_enabled"):
            return f"-{CONF_CONTROL_HOSTS}"
        if uid.startswith("access_"):
            return f"-{CONF_CONTROL_ACCESS}"
        if uid.startswith("stream_"):
            return f"-{CONF_CONTROL_STREAMS}"
        if uid.startswith("redirect_"):
            return f"-{CONF_CONTROL_REDIRECTS}"
    return None


def _remove_replaced_entities(hass: HomeAssistant, entry: ZoraxyConfigEntry) -> None:
    """Nach einer Optionsänderung verwaiste Schalter bzw. Binärsensoren entfernen."""
    registry = er.async_get(hass)
    for entity in er.async_entries_for_config_entry(registry, entry.entry_id):
        rule = _replaced_by_option(entity, entry.entry_id)
        if rule is None:
            continue
        enabled = option_enabled(entry, rule[1:])
        if (rule[0] == "+" and not enabled) or (rule[0] == "-" and enabled):
            registry.async_remove(entity.entity_id)


def option_enabled(entry: ZoraxyConfigEntry, option: str) -> bool:
    return bool(entry.options.get(option, False))
