"""Schalter (nur wenn in den Optionen freigegeben)."""

from __future__ import annotations

from collections.abc import Awaitable
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import ZoraxyConfigEntry, option_enabled
from .api import ZoraxyError
from .binary_sensor import access_attributes
from .const import (
    CONF_CONTROL_ACCESS,
    CONF_CONTROL_HOSTS,
    CONF_CONTROL_PROXY,
    CONF_CONTROL_REDIRECTS,
    CONF_CONTROL_STREAMS,
)
from .coordinator import ZoraxyCoordinator
from .entity import ZoraxyEntity, ZoraxyHostEntity, track_items

PARALLEL_UPDATES = 1


async def run_and_refresh(coordinator: ZoraxyCoordinator, call: Awaitable[Any]) -> None:
    try:
        await call
    except ZoraxyError as err:
        raise HomeAssistantError(f"Zoraxy: {err}") from err
    await coordinator.async_request_refresh()


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ZoraxyConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data.coordinator

    if option_enabled(entry, CONF_CONTROL_PROXY):
        async_add_entities([ProxySwitch(coordinator, entry)])
    if option_enabled(entry, CONF_CONTROL_HOSTS):
        track_items(
            coordinator,
            entry,
            async_add_entities,
            lambda d: d.hosts.keys(),
            lambda domain: [HostSwitch(coordinator, entry, domain)],
        )
    if option_enabled(entry, CONF_CONTROL_ACCESS):
        track_items(
            coordinator,
            entry,
            async_add_entities,
            lambda d: d.access_rules.keys(),
            lambda rule_id: [
                AccessListSwitch(coordinator, entry, rule_id, "blacklist"),
                AccessListSwitch(coordinator, entry, rule_id, "whitelist"),
            ],
        )
    if option_enabled(entry, CONF_CONTROL_STREAMS):
        track_items(
            coordinator,
            entry,
            async_add_entities,
            lambda d: d.streams.keys(),
            lambda uuid: [StreamSwitch(coordinator, entry, uuid)],
        )
    if option_enabled(entry, CONF_CONTROL_REDIRECTS):
        track_items(
            coordinator,
            entry,
            async_add_entities,
            lambda d: d.redirects.keys(),
            lambda url: [RedirectSwitch(coordinator, entry, url)],
        )


class ProxySwitch(ZoraxyEntity, SwitchEntity):
    """Schaltet den gesamten Reverse Proxy – alle Hosts auf einmal."""

    _attr_translation_key = "proxy"

    def __init__(self, coordinator: ZoraxyCoordinator, entry: ZoraxyConfigEntry) -> None:
        super().__init__(coordinator, entry, "proxy_switch")

    @property
    def is_on(self) -> bool | None:
        return self.coordinator.data.running

    async def async_turn_on(self, **kwargs: Any) -> None:
        await run_and_refresh(self.coordinator, self.coordinator.client.set_proxy_running(True))

    async def async_turn_off(self, **kwargs: Any) -> None:
        await run_and_refresh(self.coordinator, self.coordinator.client.set_proxy_running(False))


class HostSwitch(ZoraxyHostEntity, SwitchEntity):
    _attr_translation_key = "host"

    def __init__(self, coordinator: ZoraxyCoordinator, entry: ZoraxyConfigEntry, domain: str):
        super().__init__(coordinator, entry, domain, "switch")

    @property
    def is_on(self) -> bool:
        return not self.host.get("Disabled", False)

    async def _set(self, enabled: bool) -> None:
        await run_and_refresh(
            self.coordinator, self.coordinator.client.set_host_enabled(self.domain, enabled)
        )
        # Nach dem Einschalten soll der Online-Status nicht 5 Minuten hinterherhängen.
        self.coordinator.force_uptime_refresh()

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._set(False)


class AccessListSwitch(ZoraxyEntity, SwitchEntity):
    def __init__(
        self,
        coordinator: ZoraxyCoordinator,
        entry: ZoraxyConfigEntry,
        rule_id: str,
        kind: str,
    ) -> None:
        super().__init__(coordinator, entry, f"access_{rule_id}_{kind}_switch")
        self.rule_id = rule_id
        self.kind = kind
        self._attr_translation_key = kind
        rule = coordinator.data.access_rules.get(rule_id, {})
        self._attr_translation_placeholders = {"name": rule.get("Name") or rule_id}

    @property
    def _rule(self) -> dict[str, Any]:
        return self.coordinator.data.access_rules.get(self.rule_id, {})

    @property
    def available(self) -> bool:
        return super().available and self.rule_id in self.coordinator.data.access_rules

    @property
    def is_on(self) -> bool:
        key = "BlacklistEnabled" if self.kind == "blacklist" else "WhitelistEnabled"
        return bool(self._rule.get(key))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return access_attributes(self._rule, self.kind)

    async def _set(self, enabled: bool) -> None:
        client = self.coordinator.client
        call = (
            client.set_blacklist_enabled(self.rule_id, enabled)
            if self.kind == "blacklist"
            else client.set_whitelist_enabled(self.rule_id, enabled)
        )
        await run_and_refresh(self.coordinator, call)

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._set(False)


class StreamSwitch(ZoraxyEntity, SwitchEntity):
    _attr_translation_key = "stream"

    def __init__(self, coordinator: ZoraxyCoordinator, entry: ZoraxyConfigEntry, uuid: str):
        super().__init__(coordinator, entry, f"stream_{uuid}_switch")
        self.uuid = uuid
        stream = coordinator.data.streams.get(uuid, {})
        self._attr_translation_placeholders = {"name": stream.get("Name") or uuid}

    @property
    def available(self) -> bool:
        return super().available and self.uuid in self.coordinator.data.streams

    @property
    def is_on(self) -> bool:
        return bool(self.coordinator.data.streams.get(self.uuid, {}).get("Running"))

    async def async_turn_on(self, **kwargs: Any) -> None:
        await run_and_refresh(
            self.coordinator, self.coordinator.client.set_stream_running(self.uuid, True)
        )

    async def async_turn_off(self, **kwargs: Any) -> None:
        await run_and_refresh(
            self.coordinator, self.coordinator.client.set_stream_running(self.uuid, False)
        )


class RedirectSwitch(ZoraxyEntity, SwitchEntity):
    _attr_translation_key = "redirect"

    def __init__(self, coordinator: ZoraxyCoordinator, entry: ZoraxyConfigEntry, url: str):
        super().__init__(coordinator, entry, f"redirect_{url}_switch")
        self.url = url
        self._attr_translation_placeholders = {"name": url}

    @property
    def available(self) -> bool:
        return super().available and self.url in self.coordinator.data.redirects

    @property
    def is_on(self) -> bool:
        return bool(self.coordinator.data.redirects.get(self.url, {}).get("Enabled"))

    async def async_turn_on(self, **kwargs: Any) -> None:
        await run_and_refresh(
            self.coordinator, self.coordinator.client.set_redirect_enabled(self.url, True)
        )

    async def async_turn_off(self, **kwargs: Any) -> None:
        await run_and_refresh(
            self.coordinator, self.coordinator.client.set_redirect_enabled(self.url, False)
        )
