"""Aktionen zum Sperren und Entsperren von IP-Adressen."""

from __future__ import annotations

import ipaddress
from typing import TYPE_CHECKING

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
import voluptuous as vol

from .api import ZoraxyError
from .const import CONF_CONTROL_ACCESS, DOMAIN

if TYPE_CHECKING:
    from . import ZoraxyConfigEntry

ATTR_CONFIG_ENTRY_ID = "config_entry_id"
ATTR_IP = "ip"
ATTR_RULE = "rule"
ATTR_COMMENT = "comment"


def _ip_or_cidr(value: str) -> str:
    value = str(value).strip()
    try:
        ipaddress.ip_network(value, strict=False)
    except ValueError as err:
        raise vol.Invalid(f"invalid IP address or range: {value}") from err
    return value


BASE = {
    vol.Optional(ATTR_CONFIG_ENTRY_ID): cv.string,
    vol.Required(ATTR_IP): _ip_or_cidr,
    vol.Optional(ATTR_RULE, default="default"): cv.string,
}
BLOCK_SCHEMA = vol.Schema({**BASE, vol.Optional(ATTR_COMMENT, default=""): cv.string})
UNBLOCK_SCHEMA = vol.Schema(BASE)


def _entry(hass: HomeAssistant, call: ServiceCall) -> ZoraxyConfigEntry:
    entries = [
        e for e in hass.config_entries.async_entries(DOMAIN) if e.state is ConfigEntryState.LOADED
    ]
    if entry_id := call.data.get(ATTR_CONFIG_ENTRY_ID):
        entries = [e for e in entries if e.entry_id == entry_id]
        if not entries:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="entry_not_found"
            )
    if len(entries) != 1:
        raise ServiceValidationError(translation_domain=DOMAIN, translation_key="entry_ambiguous")
    entry = entries[0]
    if not entry.options.get(CONF_CONTROL_ACCESS, False):
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="control_disabled",
            translation_placeholders={"title": entry.title},
        )
    return entry


def _rule_id(entry: ZoraxyConfigEntry, value: str) -> str:
    rules = entry.runtime_data.coordinator.data.access_rules
    needle = value.strip().lower()
    for rule_id, rule in rules.items():
        if needle in (rule_id.lower(), (rule.get("Name") or "").lower()):
            return rule_id
    raise ServiceValidationError(
        translation_domain=DOMAIN,
        translation_key="rule_not_found",
        translation_placeholders={"rule": value},
    )


def async_setup_services(hass: HomeAssistant) -> None:
    async def block_ip(call: ServiceCall) -> None:
        entry = _entry(hass, call)
        coordinator = entry.runtime_data.coordinator
        rule_id = _rule_id(entry, call.data[ATTR_RULE])
        try:
            await coordinator.client.blacklist_ip(
                rule_id, call.data[ATTR_IP], call.data[ATTR_COMMENT]
            )
        except ZoraxyError as err:
            raise HomeAssistantError(f"Zoraxy: {err}") from err
        await coordinator.async_request_refresh()

    async def unblock_ip(call: ServiceCall) -> None:
        entry = _entry(hass, call)
        coordinator = entry.runtime_data.coordinator
        rule_id = _rule_id(entry, call.data[ATTR_RULE])
        try:
            await coordinator.client.unblacklist_ip(rule_id, call.data[ATTR_IP])
        except ZoraxyError as err:
            raise HomeAssistantError(f"Zoraxy: {err}") from err
        await coordinator.async_request_refresh()

    hass.services.async_register(DOMAIN, "block_ip", block_ip, schema=BLOCK_SCHEMA)
    hass.services.async_register(DOMAIN, "unblock_ip", unblock_ip, schema=UNBLOCK_SCHEMA)
