"""Services for the PC Pulse integration."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
    callback,
)
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr

from .api import AgentError
from .const import (
    ATTR_ACTION,
    ATTR_COMMAND,
    ATTR_DELAY,
    ATTR_LIMIT,
    ATTR_NAME,
    ATTR_PID,
    ATTR_SORT,
    DOMAIN,
    POWER_ACTIONS,
    SERVICE_GET_PROCESSES,
    SERVICE_KILL_PROCESS,
    SERVICE_POWER,
    SERVICE_RUN_COMMAND,
    SERVICE_WAKE,
)
from .coordinator import ComputersCoordinator

ATTR_DEVICE_ID = "device_id"

DEVICE_SCHEMA = {vol.Required(ATTR_DEVICE_ID): vol.All(cv.ensure_list, [cv.string])}

POWER_SCHEMA = vol.Schema(
    {
        **DEVICE_SCHEMA,
        vol.Required(ATTR_ACTION): vol.In(POWER_ACTIONS),
        vol.Optional(ATTR_DELAY, default=0): vol.All(vol.Coerce(int), vol.Range(min=0)),
    }
)
KILL_SCHEMA = vol.All(
    vol.Schema(
        {
            **DEVICE_SCHEMA,
            vol.Optional(ATTR_PID): vol.Coerce(int),
            vol.Optional(ATTR_NAME): cv.string,
        }
    ),
    cv.has_at_least_one_key(ATTR_PID, ATTR_NAME),
)
COMMAND_SCHEMA = vol.Schema({**DEVICE_SCHEMA, vol.Required(ATTR_COMMAND): cv.string})
WAKE_SCHEMA = vol.Schema(DEVICE_SCHEMA)
PROCESSES_SCHEMA = vol.Schema(
    {
        **DEVICE_SCHEMA,
        vol.Optional(ATTR_SORT, default="cpu"): vol.In(["cpu", "memory", "name", "pid"]),
        vol.Optional(ATTR_LIMIT, default=25): vol.All(vol.Coerce(int), vol.Range(min=1)),
    }
)


def _coordinators(hass: HomeAssistant, call: ServiceCall) -> list[ComputersCoordinator]:
    registry = dr.async_get(hass)
    result: list[ComputersCoordinator] = []
    for device_id in call.data[ATTR_DEVICE_ID]:
        device = registry.async_get(device_id)
        if device is None:
            raise ServiceValidationError(f"Unknown device {device_id}")
        # Devices belong to a single config entry since 2026.10.
        if (single := getattr(device, "config_entry_id", None)) is not None:
            entry_ids: Iterable[str] = (single,)
        else:
            entry_ids = device.config_entries
        for entry_id in entry_ids:
            entry = hass.config_entries.async_get_entry(entry_id)
            if (
                entry is not None
                and entry.domain == DOMAIN
                and entry.state is ConfigEntryState.LOADED
            ):
                result.append(entry.runtime_data)
                break
        else:
            raise ServiceValidationError(f"Device {device_id} is not a loaded computer")
    return result


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register services."""

    async def power(call: ServiceCall) -> None:
        for coordinator in _coordinators(hass, call):
            await coordinator.async_power(call.data[ATTR_ACTION], call.data[ATTR_DELAY])

    async def wake(call: ServiceCall) -> None:
        for coordinator in _coordinators(hass, call):
            await coordinator.async_wake()

    async def kill_process(call: ServiceCall) -> ServiceResponse:
        results: dict[str, Any] = {}
        for coordinator in _coordinators(hass, call):
            try:
                results[coordinator.config_entry.title] = await coordinator.client.kill(
                    call.data.get(ATTR_PID), call.data.get(ATTR_NAME)
                )
            except AgentError as err:
                raise HomeAssistantError(
                    f"Killing process on {coordinator.config_entry.title} failed: {err}"
                ) from err
            await coordinator.async_request_refresh()
        return results if call.return_response else None

    async def run_command(call: ServiceCall) -> None:
        for coordinator in _coordinators(hass, call):
            try:
                await coordinator.client.run_command(call.data[ATTR_COMMAND])
            except AgentError as err:
                raise HomeAssistantError(
                    f"Command failed on {coordinator.config_entry.title}: {err}"
                ) from err

    async def get_processes(call: ServiceCall) -> ServiceResponse:
        results: dict[str, Any] = {}
        for coordinator in _coordinators(hass, call):
            try:
                results[coordinator.config_entry.title] = await coordinator.client.processes(
                    call.data[ATTR_SORT], call.data[ATTR_LIMIT]
                )
            except AgentError as err:
                raise HomeAssistantError(
                    f"Listing processes on {coordinator.config_entry.title} failed: {err}"
                ) from err
        return {"computers": results}

    hass.services.async_register(DOMAIN, SERVICE_POWER, power, schema=POWER_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_WAKE, wake, schema=WAKE_SCHEMA)
    hass.services.async_register(
        DOMAIN,
        SERVICE_KILL_PROCESS,
        kill_process,
        schema=KILL_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN, SERVICE_RUN_COMMAND, run_command, schema=COMMAND_SCHEMA
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_GET_PROCESSES,
        get_processes,
        schema=PROCESSES_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
