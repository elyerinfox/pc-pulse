"""Config flow for the PC Pulse integration."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_HOST, CONF_PORT, CONF_SCAN_INTERVAL, CONF_TOKEN
from homeassistant.core import callback
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.device_registry import format_mac
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo

from .api import AgentAuthError, AgentClient, AgentConnectionError, AgentError
from .const import (
    CONF_BROADCAST,
    CONF_MAC,
    DEFAULT_BROADCAST,
    DEFAULT_PORT,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MIN_SCAN_INTERVAL,
)

TOKEN_SELECTOR = selector.TextSelector(
    selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
)


def _user_schema(defaults: Mapping[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_HOST, default=defaults.get(CONF_HOST, "")): str,
            vol.Required(CONF_PORT, default=defaults.get(CONF_PORT, DEFAULT_PORT)): vol.All(
                vol.Coerce(int), vol.Range(min=1, max=65535)
            ),
            vol.Required(CONF_TOKEN, default=defaults.get(CONF_TOKEN, "")): TOKEN_SELECTOR,
        }
    )


class ComputersConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow."""

    VERSION = 1

    def __init__(self) -> None:
        self._host: str | None = None
        self._port: int = DEFAULT_PORT
        self._name: str | None = None

    async def _validate(self, data: Mapping[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
        client = AgentClient(
            async_get_clientsession(self.hass), data[CONF_HOST], data[CONF_PORT], data[CONF_TOKEN]
        )
        try:
            status = await client.status()
        except AgentAuthError:
            return None, "invalid_auth"
        except AgentConnectionError:
            return None, "cannot_connect"
        except AgentError:
            return None, "unknown"
        return status, None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manual setup."""
        errors: dict[str, str] = {}
        if user_input is not None:
            status, error = await self._validate(user_input)
            if error:
                errors["base"] = error
            else:
                assert status is not None
                await self.async_set_unique_id(status["id"])
                self._abort_if_unique_id_configured(
                    updates={
                        CONF_HOST: user_input[CONF_HOST],
                        CONF_PORT: user_input[CONF_PORT],
                        CONF_TOKEN: user_input[CONF_TOKEN],
                    }
                )
                return self.async_create_entry(title=status["name"], data=user_input)
        return self.async_show_form(
            step_id="user", data_schema=_user_schema(user_input or {}), errors=errors
        )

    async def async_step_zeroconf(
        self, discovery_info: ZeroconfServiceInfo
    ) -> ConfigFlowResult:
        """Agent discovered on the network."""
        agent_id = discovery_info.properties.get("id")
        if not agent_id:
            return self.async_abort(reason="not_supported")
        self._host = str(discovery_info.ip_address)
        self._port = discovery_info.port or DEFAULT_PORT
        self._name = discovery_info.properties.get("name") or discovery_info.hostname

        await self.async_set_unique_id(agent_id)
        self._abort_if_unique_id_configured(
            updates={CONF_HOST: self._host, CONF_PORT: self._port}
        )
        self.context["title_placeholders"] = {"name": self._name}
        return await self.async_step_zeroconf_confirm()

    async def async_step_zeroconf_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the token of a discovered agent."""
        errors: dict[str, str] = {}
        if user_input is not None:
            data = {
                CONF_HOST: self._host,
                CONF_PORT: self._port,
                CONF_TOKEN: user_input[CONF_TOKEN],
            }
            status, error = await self._validate(data)
            if error:
                errors["base"] = error
            else:
                assert status is not None
                return self.async_create_entry(title=status["name"], data=data)
        return self.async_show_form(
            step_id="zeroconf_confirm",
            data_schema=vol.Schema({vol.Required(CONF_TOKEN): TOKEN_SELECTOR}),
            description_placeholders={"name": self._name or "", "host": self._host or ""},
            errors=errors,
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """The agent rejected the token."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for a new token."""
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            data = {**entry.data, CONF_TOKEN: user_input[CONF_TOKEN]}
            status, error = await self._validate(data)
            if error:
                errors["base"] = error
            else:
                assert status is not None
                await self.async_set_unique_id(status["id"])
                self._abort_if_unique_id_mismatch(reason="wrong_computer")
                return self.async_update_reload_and_abort(entry, data=data)
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_TOKEN): TOKEN_SELECTOR}),
            description_placeholders={"name": entry.title},
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Change host, port or token."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            status, error = await self._validate(user_input)
            if error:
                errors["base"] = error
            else:
                assert status is not None
                await self.async_set_unique_id(status["id"])
                self._abort_if_unique_id_mismatch(reason="wrong_computer")
                return self.async_update_reload_and_abort(entry, data=user_input)
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=_user_schema(user_input or entry.data),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Options flow."""
        return ComputersOptionsFlow()


class ComputersOptionsFlow(OptionsFlow):
    """Polling interval and Wake-on-LAN settings."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            mac = (user_input.get(CONF_MAC) or "").strip()
            if mac:
                digits = "".join(c for c in mac if c.isalnum())
                if len(digits) != 12 or any(c not in "0123456789abcdefABCDEF" for c in digits):
                    errors[CONF_MAC] = "invalid_mac"
                else:
                    user_input[CONF_MAC] = format_mac(mac)
            else:
                user_input.pop(CONF_MAC, None)
            if not errors:
                return self.async_create_entry(data=user_input)

        options = self.config_entry.options
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_SCAN_INTERVAL,
                    default=options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
                ): vol.All(vol.Coerce(int), vol.Range(min=MIN_SCAN_INTERVAL, max=3600)),
                vol.Optional(
                    CONF_MAC,
                    description={"suggested_value": options.get(CONF_MAC)},
                ): str,
                vol.Required(
                    CONF_BROADCAST,
                    default=options.get(CONF_BROADCAST, DEFAULT_BROADCAST),
                ): str,
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema, errors=errors)
