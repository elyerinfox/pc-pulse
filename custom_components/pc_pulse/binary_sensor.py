"""Binary sensors for the PC Pulse integration."""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import ComputersConfigEntry
from .entity import ComputerEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ComputersConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up binary sensors."""
    coordinator = entry.runtime_data
    entities: list[BinarySensorEntity] = [
        OnlineSensor(coordinator, "online"),
        UserActiveSensor(coordinator, "user_active"),
        LockSensor(coordinator, "screen_lock"),
    ]
    if (coordinator.layout.get("capabilities") or {}).get("os_updates"):
        entities.append(RestartRequiredSensor(coordinator, "restart_required"))
    if coordinator.layout.get("has_battery"):
        entities.append(ChargingSensor(coordinator, "battery_charging"))
    async_add_entities(entities)


class OnlineSensor(ComputerEntity, BinarySensorEntity):
    """Whether the agent is reachable. Never unavailable."""

    _attr_translation_key = "online"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY

    @property
    def available(self) -> bool:
        return True

    @property
    def is_on(self) -> bool:
        return self.coordinator.last_update_success


class UserActiveSensor(ComputerEntity, BinarySensorEntity):
    """Whether someone used the keyboard/mouse recently."""

    _attr_translation_key = "user_active"
    _attr_device_class = BinarySensorDeviceClass.OCCUPANCY

    @property
    def is_on(self) -> bool | None:
        return self.coordinator.data["activity"].get("active")

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        activity = self.coordinator.data["activity"]
        return {
            "idle_seconds": activity.get("idle_seconds"),
            "last_active": activity.get("last_active"),
        }


class LockSensor(ComputerEntity, BinarySensorEntity):
    """Screen lock state (on = unlocked, matching the lock device class)."""

    _attr_translation_key = "screen_lock"
    _attr_device_class = BinarySensorDeviceClass.LOCK

    @property
    def is_on(self) -> bool | None:
        locked = self.coordinator.data["activity"].get("locked")
        return None if locked is None else not locked


class ChargingSensor(ComputerEntity, BinarySensorEntity):
    """Whether the battery is charging."""

    _attr_translation_key = "battery_charging"
    _attr_device_class = BinarySensorDeviceClass.BATTERY_CHARGING

    @property
    def available(self) -> bool:
        return super().available and self.coordinator.data.get("battery") is not None

    @property
    def is_on(self) -> bool | None:
        return self.coordinator.data["battery"]["charging"]


class RestartRequiredSensor(ComputerEntity, BinarySensorEntity):
    """Whether installed OS updates are waiting for a restart."""

    _attr_translation_key = "restart_required"

    def _reboot(self) -> bool | None:
        return ((self.coordinator.data or {}).get("os_updates") or {}).get("reboot_required")

    @property
    def available(self) -> bool:
        return super().available and self._reboot() is not None

    @property
    def is_on(self) -> bool | None:
        return self._reboot()
