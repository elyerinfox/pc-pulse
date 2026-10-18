"""Data coordinator for a single computer."""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.device_registry import CONNECTION_NETWORK_MAC, DeviceInfo
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import (
    AgentAuthError,
    AgentClient,
    AgentConnectionError,
    AgentError,
    send_magic_packet,
)
from .const import (
    CONF_BROADCAST,
    CONF_MAC,
    DEFAULT_BROADCAST,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    POWER_OFF_GRACE,
)

_LOGGER = logging.getLogger(__name__)

type ComputersConfigEntry = ConfigEntry[ComputersCoordinator]

STORAGE_VERSION = 1


def layout_from_status(data: dict[str, Any]) -> dict[str, Any]:
    """Extract the parts of a status that decide which entities exist."""
    return {
        "id": data.get("id"),
        "name": data.get("name"),
        "hostname": data.get("hostname"),
        "agent_version": data.get("agent_version"),
        "os": data.get("os") or {},
        "cpu_brand": (data.get("cpu") or {}).get("brand"),
        "capabilities": data.get("capabilities") or {},
        "disks": sorted(d["mount_point"] for d in data.get("disks") or []),
        "has_temperature": bool(data.get("temperatures")),
        "primary_mac": data.get("primary_mac"),
        "primary_ip": data.get("primary_ip"),
    }


class ComputersCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Polls one agent and keeps a cached layout so entities survive the PC being off."""

    config_entry: ComputersConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ComputersConfigEntry,
        client: AgentClient,
    ) -> None:
        interval = entry.options.get("scan_interval", DEFAULT_SCAN_INTERVAL)
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} {entry.title}",
            update_interval=timedelta(seconds=interval),
        )
        self.client = client
        self.layout: dict[str, Any] = {}
        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}"
        )
        # Optimistic power state after a command, until polling confirms it.
        self._expected_online: bool | None = None
        self._expected_until = dt_util.utcnow()

    async def async_load_layout(self) -> None:
        """Load the cached layout from storage."""
        self.layout = await self._store.async_load() or {}

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            data = await self.client.status()
        except AgentAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except AgentConnectionError as err:
            raise UpdateFailed(f"Agent unreachable: {err}") from err
        except AgentError as err:
            raise UpdateFailed(str(err)) from err

        layout = layout_from_status(data)
        if layout != self.layout:
            self.layout = layout
            await self._store.async_save(layout)
        return data

    # ----- helpers used by entities -----

    @property
    def agent_id(self) -> str:
        """Stable id of the agent."""
        return self.config_entry.unique_id or self.config_entry.entry_id

    @property
    def mac(self) -> str | None:
        """MAC used for Wake-on-LAN (user override first)."""
        return self.config_entry.options.get(CONF_MAC) or self.layout.get("primary_mac")

    @property
    def online(self) -> bool:
        """Whether the computer is considered on."""
        if (
            self._expected_online is not None
            and dt_util.utcnow() < self._expected_until
        ):
            # A successful poll after a wake request confirms it right away.
            if self._expected_online and self.last_update_success:
                return True
            return self._expected_online
        return self.last_update_success

    def expect_online(self, online: bool) -> None:
        """Optimistically show a power state for a while after a command."""
        self._expected_online = online
        self._expected_until = dt_util.utcnow() + POWER_OFF_GRACE
        self.async_update_listeners()

    def supports(self, action: str) -> bool:
        """Whether the agent advertised a power action."""
        return action in (self.layout.get("capabilities") or {}).get("power", [])

    @property
    def device_info(self) -> DeviceInfo:
        """Device registry info."""
        os_info = self.layout.get("os") or {}
        connections = set()
        if mac := self.mac:
            connections.add((CONNECTION_NETWORK_MAC, mac.lower()))
        return DeviceInfo(
            identifiers={(DOMAIN, self.agent_id)},
            connections=connections,
            name=self.config_entry.title,
            manufacturer=os_info.get("name") or "Computer",
            model=os_info.get("long_version") or os_info.get("name"),
            sw_version=os_info.get("version"),
            hw_version=self.layout.get("cpu_brand"),
            configuration_url=None,
        )

    async def async_power(self, action: str, delay: int = 0) -> None:
        """Send a power action to the agent."""
        try:
            await self.client.power(action, delay)
        except AgentError as err:
            raise HomeAssistantError(
                f"Could not {action.replace('_', ' ')} {self.config_entry.title}: {err}"
            ) from err
        if action in ("shutdown", "sleep", "hibernate") and delay == 0:
            self.expect_online(False)
        elif action == "restart":
            self.expect_online(True)

    async def async_wake(self) -> None:
        """Send a Wake-on-LAN packet."""
        mac = self.mac
        if not mac:
            raise HomeAssistantError(
                f"No MAC address known for {self.config_entry.title}; "
                "set one in the integration options"
            )
        broadcast = self.config_entry.options.get(CONF_BROADCAST) or DEFAULT_BROADCAST
        try:
            await self.hass.async_add_executor_job(send_magic_packet, mac, broadcast)
        except (OSError, ValueError) as err:
            raise HomeAssistantError(f"Wake-on-LAN failed: {err}") from err
        self.expect_online(True)
