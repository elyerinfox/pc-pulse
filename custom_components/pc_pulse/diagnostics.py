"""Diagnostics for the PC Pulse integration."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_TOKEN
from homeassistant.core import HomeAssistant

from .coordinator import ComputersConfigEntry

TO_REDACT = {CONF_TOKEN}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ComputersConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator = entry.runtime_data
    return {
        "entry": async_redact_data(entry.as_dict(), TO_REDACT),
        "layout": coordinator.layout,
        "last_update_success": coordinator.last_update_success,
        "data": coordinator.data,
    }
