"""Power switch: on = Wake-on-LAN, off = shutdown."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchDeviceClass, SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import ComputersConfigEntry
from .entity import ComputerEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ComputersConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the power switch."""
    coordinator = entry.runtime_data
    if coordinator.supports("shutdown"):
        async_add_entities([PowerSwitch(coordinator, "power")])


class PowerSwitch(ComputerEntity, SwitchEntity):
    """Shows whether the computer is on; turns it on via WoL and off via shutdown."""

    _attr_translation_key = "power"
    _attr_device_class = SwitchDeviceClass.SWITCH

    @property
    def available(self) -> bool:
        return True

    @property
    def is_on(self) -> bool:
        return self.coordinator.online

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.async_wake()

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_power("shutdown")
