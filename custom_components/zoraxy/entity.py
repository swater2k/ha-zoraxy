"""Basis-Entitäten und Helfer für dynamisch auftauchende Objekte."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import TYPE_CHECKING, Any

from homeassistant.core import callback
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import ZoraxyCoordinator, ZoraxyData

if TYPE_CHECKING:
    from . import ZoraxyConfigEntry

# HA 2026.8 ersetzt DeviceInfo["via_device"] durch "via_device_id"; ältere
# Versionen kennen nur den alten Schlüssel.
_HAS_VIA_DEVICE_ID = "via_device_id" in DeviceInfo.__annotations__


def host_device_id(entry_id: str, domain: str) -> str:
    return f"{entry_id}_host_{domain}"


def find_device(
    registry: dr.DeviceRegistry, identifier: str, entry_id: str
) -> dr.DeviceEntry | None:
    """Gerät dieses Eintrags suchen – mit der neuen API ab HA 2026.9, sonst der alten."""
    if hasattr(registry, "async_get_device_by_identifier"):
        return registry.async_get_device_by_identifier((DOMAIN, identifier), entry_id)
    return registry.async_get_device(identifiers={(DOMAIN, identifier)})


def _via_server(entry: ZoraxyConfigEntry) -> dict[str, Any]:
    if _HAS_VIA_DEVICE_ID:
        return {"via_device_id": entry.runtime_data.server_device_id}
    return {"via_device": (DOMAIN, entry.entry_id)}


class ZoraxyEntity(CoordinatorEntity[ZoraxyCoordinator]):
    """Entität am Server-Gerät."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: ZoraxyCoordinator, entry: ZoraxyConfigEntry, key: str) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, entry.entry_id)})


class ZoraxyHostEntity(CoordinatorEntity[ZoraxyCoordinator]):
    """Entität an einem Proxy-Host-Gerät."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: ZoraxyCoordinator,
        entry: ZoraxyConfigEntry,
        domain: str,
        key: str,
    ) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self.domain = domain
        self._attr_unique_id = f"{entry.entry_id}_host_{domain}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, host_device_id(entry.entry_id, domain))},
            name=domain,
            manufacturer="Zoraxy",
            model="Proxy host",
            configuration_url=f"https://{domain}",
            **_via_server(entry),
        )

    @property
    def host(self) -> dict[str, Any]:
        return self.coordinator.data.hosts.get(self.domain, {})

    @property
    def available(self) -> bool:
        return super().available and self.domain in self.coordinator.data.hosts


@callback
def track_items(
    coordinator: ZoraxyCoordinator,
    entry: ZoraxyConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
    get_ids: Callable[[ZoraxyData], Iterable[str]],
    create: Callable[[str], list[Entity]],
) -> None:
    """Legt Entitäten für neue Objekte an und räumt verschwundene auf."""
    known: dict[str, list[Entity]] = {}

    @callback
    def _sync() -> None:
        if coordinator.data is None:
            return
        current = set(get_ids(coordinator.data))
        new: list[Entity] = []
        for item_id in current - known.keys():
            entities = create(item_id)
            known[item_id] = entities
            new.extend(entities)
        if new:
            async_add_entities(new)
        registry = er.async_get(coordinator.hass)
        for item_id in known.keys() - current:
            for entity in known.pop(item_id):
                if entity.entity_id and registry.async_get(entity.entity_id):
                    registry.async_remove(entity.entity_id)

    _sync()
    entry.async_on_unload(coordinator.async_add_listener(_sync))


@callback
def remove_stale_host_devices(coordinator: ZoraxyCoordinator, entry: ZoraxyConfigEntry) -> None:
    """Geräte gelöschter Proxy-Hosts nach jedem Abruf entfernen."""
    registry = dr.async_get(coordinator.hass)
    prefix = f"{entry.entry_id}_host_"

    @callback
    def _cleanup() -> None:
        if coordinator.data is None or not coordinator.last_update_success:
            return
        for device in dr.async_entries_for_config_entry(registry, entry.entry_id):
            for domain_, identifier in device.identifiers:
                if (
                    domain_ == DOMAIN
                    and identifier.startswith(prefix)
                    and identifier[len(prefix) :] not in coordinator.data.hosts
                ):
                    registry.async_remove_device(device.id)
                    break

    entry.async_on_unload(coordinator.async_add_listener(_cleanup))
