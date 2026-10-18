"""Buttons for power actions, Wake-on-LAN and custom commands."""

from __future__ import annotations

from homeassistant.components.button import ButtonDeviceClass, ButtonEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api import AgentError
from .const import POWER_ACTIONS
from .coordinator import ComputersConfigEntry, ComputersCoordinator
from .entity import ComputerEntity

# Actions that are disabled by default because they are rarely wanted on a dashboard.
HIDDEN_BY_DEFAULT = {"logoff", "monitor_on"}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ComputersConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up buttons."""
    coordinator = entry.runtime_data
    entities: list[ButtonEntity] = [
        PowerButton(coordinator, action)
        for action in POWER_ACTIONS
        if coordinator.supports(action)
    ]
    entities.append(WakeButton(coordinator, "wake"))
    known_commands: set[str] = set(
        (coordinator.layout.get("capabilities") or {}).get("commands", [])
    )
    entities.extend(CommandButton(coordinator, name) for name in known_commands)
    async_add_entities(entities)

    @callback
    def _add_new_commands() -> None:
        current = set((coordinator.layout.get("capabilities") or {}).get("commands", []))
        new = current - known_commands
        if new:
            known_commands.update(new)
            async_add_entities(CommandButton(coordinator, name) for name in new)

    entry.async_on_unload(coordinator.async_add_listener(_add_new_commands))


class PowerButton(ComputerEntity, ButtonEntity):
    """Runs a power action on the computer."""

    def __init__(self, coordinator: ComputersCoordinator, action: str) -> None:
        super().__init__(coordinator, f"power_{action}")
        self._action = action
        self._attr_translation_key = action
        if action == "restart":
            self._attr_device_class = ButtonDeviceClass.RESTART
        self._attr_entity_registry_enabled_default = action not in HIDDEN_BY_DEFAULT

    async def async_press(self) -> None:
        await self.coordinator.async_power(self._action)


class WakeButton(ComputerEntity, ButtonEntity):
    """Sends a Wake-on-LAN packet. Available while the computer is off."""

    _attr_translation_key = "wake"

    @property
    def available(self) -> bool:
        return self.coordinator.mac is not None

    async def async_press(self) -> None:
        await self.coordinator.async_wake()


class CommandButton(ComputerEntity, ButtonEntity):
    """Runs a command defined in the agent's config."""

    _attr_translation_key = "command"

    def __init__(self, coordinator: ComputersCoordinator, name: str) -> None:
        super().__init__(coordinator, f"command_{name}")
        self._command = name
        self._attr_translation_placeholders = {"name": name.replace("_", " ")}

    @property
    def available(self) -> bool:
        commands = (self.coordinator.layout.get("capabilities") or {}).get("commands", [])
        return super().available and self._command in commands

    async def async_press(self) -> None:
        try:
            await self.coordinator.client.run_command(self._command)
        except AgentError as err:
            raise HomeAssistantError(f"Command '{self._command}' failed: {err}") from err
