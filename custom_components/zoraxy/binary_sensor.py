"""Binärsensoren."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import ZoraxyConfigEntry, option_enabled
from .const import (
    CONF_CONTROL_ACCESS,
    CONF_CONTROL_HOSTS,
    CONF_CONTROL_REDIRECTS,
    CONF_CONTROL_STREAMS,
)
from .coordinator import ZoraxyCoordinator
from .entity import ZoraxyEntity, ZoraxyHostEntity, remove_stale_host_devices, track_items

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ZoraxyConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data.coordinator
    async_add_entities([ProxyRunning(coordinator, entry)])
    remove_stale_host_devices(coordinator, entry)

    def _host_entities(domain: str) -> list[BinarySensorEntity]:
        entities: list[BinarySensorEntity] = [HostOnline(coordinator, entry, domain)]
        # Mit freigegebener Steuerung übernimmt der Schalter diese Rolle.
        if not option_enabled(entry, CONF_CONTROL_HOSTS):
            entities.append(HostEnabled(coordinator, entry, domain))
        return entities

    track_items(coordinator, entry, async_add_entities, lambda d: d.hosts.keys(), _host_entities)

    if not option_enabled(entry, CONF_CONTROL_ACCESS):
        track_items(
            coordinator,
            entry,
            async_add_entities,
            lambda d: d.access_rules.keys(),
            lambda rule_id: [
                AccessListActive(coordinator, entry, rule_id, "blacklist"),
                AccessListActive(coordinator, entry, rule_id, "whitelist"),
            ],
        )
    if not option_enabled(entry, CONF_CONTROL_STREAMS):
        track_items(
            coordinator,
            entry,
            async_add_entities,
            lambda d: d.streams.keys(),
            lambda uuid: [StreamRunning(coordinator, entry, uuid)],
        )
    if not option_enabled(entry, CONF_CONTROL_REDIRECTS):
        track_items(
            coordinator,
            entry,
            async_add_entities,
            lambda d: d.redirects.keys(),
            lambda url: [RedirectEnabled(coordinator, entry, url)],
        )


class ProxyRunning(ZoraxyEntity, BinarySensorEntity):
    _attr_translation_key = "proxy_running"
    _attr_device_class = BinarySensorDeviceClass.RUNNING

    def __init__(self, coordinator: ZoraxyCoordinator, entry: ZoraxyConfigEntry) -> None:
        super().__init__(coordinator, entry, "proxy_running")

    @property
    def is_on(self) -> bool | None:
        return self.coordinator.data.running


class HostOnline(ZoraxyHostEntity, BinarySensorEntity):
    """Ergebnis des Zoraxy-Uptime-Monitors für den Upstream dieses Hosts."""

    _attr_translation_key = "upstream_online"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY

    def __init__(self, coordinator: ZoraxyCoordinator, entry: ZoraxyConfigEntry, domain: str):
        super().__init__(coordinator, entry, domain, "upstream_online")

    @property
    def available(self) -> bool:
        # Deaktivierte Hosts oder abgeschalteter Monitor liefern keine Daten.
        uptime = self.coordinator.data.uptime.get(self.domain)
        return super().available and uptime is not None and uptime.online is not None

    @property
    def is_on(self) -> bool | None:
        uptime = self.coordinator.data.uptime.get(self.domain)
        return uptime.online if uptime else None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        uptime = self.coordinator.data.uptime.get(self.domain)
        if uptime is None:
            return None
        return {
            "url": uptime.url,
            "status_code": uptime.status_code,
            "checked": uptime.checked.isoformat() if uptime.checked else None,
        }


class HostEnabled(ZoraxyHostEntity, BinarySensorEntity):
    _attr_translation_key = "host_enabled"

    def __init__(self, coordinator: ZoraxyCoordinator, entry: ZoraxyConfigEntry, domain: str):
        super().__init__(coordinator, entry, domain, "enabled")

    @property
    def is_on(self) -> bool:
        return not self.host.get("Disabled", False)


class AccessListActive(ZoraxyEntity, BinarySensorEntity):
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        coordinator: ZoraxyCoordinator,
        entry: ZoraxyConfigEntry,
        rule_id: str,
        kind: str,
    ) -> None:
        super().__init__(coordinator, entry, f"access_{rule_id}_{kind}")
        self.rule_id = rule_id
        self.kind = kind
        self._attr_translation_key = kind
        rule = coordinator.data.access_rules.get(rule_id, {})
        self._attr_translation_placeholders = {"name": rule.get("Name") or rule_id}

    @property
    def available(self) -> bool:
        return super().available and self.rule_id in self.coordinator.data.access_rules

    @property
    def is_on(self) -> bool:
        rule = self.coordinator.data.access_rules.get(self.rule_id, {})
        key = "BlacklistEnabled" if self.kind == "blacklist" else "WhitelistEnabled"
        return bool(rule.get(key))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return access_attributes(
            self.coordinator.data.access_rules.get(self.rule_id, {}), self.kind
        )


def access_attributes(rule: dict[str, Any], kind: str) -> dict[str, Any]:
    """IP- und Länderlisten einer Zugriffsregel (Zoraxy schreibt „Contry“ falsch)."""
    if kind == "blacklist":
        ips = rule.get("BlackListIP") or {}
        countries = rule.get("BlackListContryCode") or rule.get("BlackListCountryCode") or {}
    else:
        ips = rule.get("WhiteListIP") or {}
        countries = rule.get("WhiteListCountryCode") or {}
    return {"ips": sorted(ips), "countries": sorted(countries)}


class StreamRunning(ZoraxyEntity, BinarySensorEntity):
    _attr_translation_key = "stream"
    _attr_device_class = BinarySensorDeviceClass.RUNNING

    def __init__(self, coordinator: ZoraxyCoordinator, entry: ZoraxyConfigEntry, uuid: str):
        super().__init__(coordinator, entry, f"stream_{uuid}")
        self.uuid = uuid
        stream = coordinator.data.streams.get(uuid, {})
        self._attr_translation_placeholders = {"name": stream.get("Name") or uuid}

    @property
    def available(self) -> bool:
        return super().available and self.uuid in self.coordinator.data.streams

    @property
    def is_on(self) -> bool:
        return bool(self.coordinator.data.streams.get(self.uuid, {}).get("Running"))


class RedirectEnabled(ZoraxyEntity, BinarySensorEntity):
    _attr_translation_key = "redirect"

    def __init__(self, coordinator: ZoraxyCoordinator, entry: ZoraxyConfigEntry, url: str):
        super().__init__(coordinator, entry, f"redirect_{url}")
        self.url = url
        self._attr_translation_placeholders = {"name": url}

    @property
    def available(self) -> bool:
        return super().available and self.url in self.coordinator.data.redirects

    @property
    def is_on(self) -> bool:
        return bool(self.coordinator.data.redirects.get(self.url, {}).get("Enabled"))
