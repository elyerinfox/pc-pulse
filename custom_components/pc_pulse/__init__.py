"""Control and monitor computers running the pc-pulse agent."""

from __future__ import annotations

from homeassistant.const import CONF_HOST, CONF_PORT, CONF_TOKEN, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store
from homeassistant.helpers.typing import ConfigType

from .api import AgentClient
from .const import DOMAIN
from .coordinator import STORAGE_VERSION, ComputersConfigEntry, ComputersCoordinator
from .services import async_setup_services

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.NOTIFY,
    Platform.SENSOR,
    Platform.SWITCH,
]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register integration-wide services."""
    async_setup_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ComputersConfigEntry) -> bool:
    """Set up a computer from a config entry."""
    client = AgentClient(
        async_get_clientsession(hass),
        entry.data[CONF_HOST],
        entry.data[CONF_PORT],
        entry.data[CONF_TOKEN],
    )
    coordinator = ComputersCoordinator(hass, entry, client)
    await coordinator.async_load_layout()

    # A computer that is switched off must still load so it can be woken up,
    # so only fail setup when we have never seen it.
    await coordinator.async_refresh()
    if not coordinator.last_update_success:
        if isinstance(coordinator.last_exception, ConfigEntryAuthFailed):
            raise coordinator.last_exception
        if not coordinator.layout:
            raise ConfigEntryNotReady(
                f"Unable to reach {entry.title}: {coordinator.last_exception}"
            )

    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    return True


async def _async_options_updated(hass: HomeAssistant, entry: ComputersConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ComputersConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(hass: HomeAssistant, entry: ComputersConfigEntry) -> None:
    """Remove cached layout when the entry is deleted."""
    await Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}").async_remove()
