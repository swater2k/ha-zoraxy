"""Buttons (nur wenn in den Optionen freigegeben)."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import ZoraxyConfigEntry, option_enabled
from .const import CONF_CONTROL_CERTIFICATES
from .coordinator import ZoraxyCoordinator
from .entity import ZoraxyEntity
from .switch import run_and_refresh

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ZoraxyConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    if option_enabled(entry, CONF_CONTROL_CERTIFICATES):
        async_add_entities([RenewCertificates(entry.runtime_data.coordinator, entry)])


class RenewCertificates(ZoraxyEntity, ButtonEntity):
    """Stößt die ACME-Erneuerung aller für Auto-Renew eingetragenen Domains an."""

    _attr_translation_key = "renew_certificates"

    def __init__(self, coordinator: ZoraxyCoordinator, entry: ZoraxyConfigEntry) -> None:
        super().__init__(coordinator, entry, "renew_certificates")

    async def async_press(self) -> None:
        await run_and_refresh(self.coordinator, self.coordinator.client.renew_certificates())
