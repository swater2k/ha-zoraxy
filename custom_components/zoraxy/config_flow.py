"""Config-, Reauth-, Reconfigure- und Options-Flow."""

from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Any
from urllib.parse import urlsplit

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.const import CONF_PASSWORD, CONF_URL, CONF_USERNAME, CONF_VERIFY_SSL
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.selector import (
    BooleanSelector,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)
import voluptuous as vol

from . import build_client
from .api import ZoraxyAuthError, ZoraxyConnectionError, ZoraxyError, normalize_url
from .const import (
    CONF_SCAN_INTERVAL,
    CONF_UPTIME_INTERVAL,
    CONTROL_OPTIONS,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_UPTIME_INTERVAL,
    DOMAIN,
    MAX_SCAN_INTERVAL,
    MAX_UPTIME_INTERVAL,
    MIN_SCAN_INTERVAL,
    MIN_UPTIME_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)

_PASSWORD = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))
# Hassfest verbietet URLs in Übersetzungen – das Beispiel kommt als Platzhalter.
PLACEHOLDERS = {"example_url": "http://192.168.1.10:8000"}


def _schema(defaults: Mapping[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_URL, default=defaults.get(CONF_URL, "")): TextSelector(),
            vol.Required(CONF_USERNAME, default=defaults.get(CONF_USERNAME, "")): TextSelector(),
            vol.Required(CONF_PASSWORD): _PASSWORD,
            vol.Required(CONF_VERIFY_SSL, default=defaults.get(CONF_VERIFY_SSL, True)): (
                BooleanSelector()
            ),
        }
    )


def _normalize(user_input: Mapping[str, Any]) -> dict[str, Any]:
    return {
        CONF_URL: normalize_url(user_input[CONF_URL]),
        CONF_USERNAME: user_input[CONF_USERNAME].strip(),
        CONF_PASSWORD: user_input[CONF_PASSWORD],
        CONF_VERIFY_SSL: bool(user_input.get(CONF_VERIFY_SSL, True)),
    }


async def validate_input(
    hass: HomeAssistant, data: dict[str, Any]
) -> tuple[dict[str, str], str | None]:
    """Anmelden und die Host-ID lesen; liefert (Fehler, eindeutige ID)."""
    client, session = build_client(hass, data)
    try:
        await client.login()
        status = await client.status()
    except ZoraxyAuthError:
        return {"base": "invalid_auth"}, None
    except ZoraxyConnectionError:
        return {"base": "cannot_connect"}, None
    except ZoraxyError:
        return {"base": "invalid_response"}, None
    except Exception:
        _LOGGER.exception("Unerwarteter Fehler bei der Validierung")
        return {"base": "unknown"}, None
    finally:
        await session.close()
    host_uuid = ((status or {}).get("Option") or {}).get("HostUUID")
    return {}, host_uuid or data[CONF_URL]


class ZoraxyConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            data = _normalize(user_input)
            errors, unique_id = await validate_input(self.hass, data)
            if not errors:
                await self.async_set_unique_id(unique_id)
                self._abort_if_unique_id_configured(updates={CONF_URL: data[CONF_URL]})
                host = urlsplit(data[CONF_URL]).hostname or data[CONF_URL]
                return self.async_create_entry(title=f"Zoraxy ({host})", data=data)
        return self.async_show_form(
            step_id="user",
            data_schema=_schema(user_input or {}),
            errors=errors,
            description_placeholders=PLACEHOLDERS,
        )

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            data = {
                **entry.data,
                CONF_USERNAME: user_input[CONF_USERNAME].strip(),
                CONF_PASSWORD: user_input[CONF_PASSWORD],
            }
            errors, _ = await validate_input(self.hass, data)
            if not errors:
                return self.async_update_reload_and_abort(entry, data_updates=data)
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_USERNAME, default=entry.data.get(CONF_USERNAME, "")
                    ): TextSelector(),
                    vol.Required(CONF_PASSWORD): _PASSWORD,
                }
            ),
            description_placeholders={"url": entry.data[CONF_URL]},
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            data = _normalize(user_input)
            errors, unique_id = await validate_input(self.hass, data)
            if not errors:
                await self.async_set_unique_id(unique_id)
                self._abort_if_unique_id_mismatch(reason="different_instance")
                return self.async_update_reload_and_abort(entry, data_updates=data)
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=_schema(user_input or dict(entry.data)),
            errors=errors,
            description_placeholders=PLACEHOLDERS,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> ZoraxyOptionsFlow:
        return ZoraxyOptionsFlow()


class ZoraxyOptionsFlow(OptionsFlowWithReload):
    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            data: dict[str, Any] = {
                CONF_SCAN_INTERVAL: int(user_input[CONF_SCAN_INTERVAL]),
                CONF_UPTIME_INTERVAL: int(user_input[CONF_UPTIME_INTERVAL]),
            }
            for option in CONTROL_OPTIONS:
                data[option] = bool(user_input.get(option, False))
            return self.async_create_entry(data=data)

        opts = self.config_entry.options
        fields: dict[Any, Any] = {
            vol.Required(
                CONF_SCAN_INTERVAL, default=opts.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
            ): NumberSelector(
                NumberSelectorConfig(
                    min=MIN_SCAN_INTERVAL,
                    max=MAX_SCAN_INTERVAL,
                    step=5,
                    unit_of_measurement="s",
                    mode=NumberSelectorMode.BOX,
                )
            ),
            vol.Required(
                CONF_UPTIME_INTERVAL,
                default=opts.get(CONF_UPTIME_INTERVAL, DEFAULT_UPTIME_INTERVAL),
            ): NumberSelector(
                NumberSelectorConfig(
                    min=MIN_UPTIME_INTERVAL,
                    max=MAX_UPTIME_INTERVAL,
                    step=30,
                    unit_of_measurement="s",
                    mode=NumberSelectorMode.BOX,
                )
            ),
        }
        for option in CONTROL_OPTIONS:
            fields[vol.Optional(option, default=opts.get(option, False))] = BooleanSelector()
        return self.async_show_form(step_id="init", data_schema=vol.Schema(fields))
