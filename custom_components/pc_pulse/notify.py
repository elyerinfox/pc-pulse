"""Desktop notifications on the computer."""

from __future__ import annotations

from homeassistant.components.notify import NotifyEntity, NotifyEntityFeature
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api import AgentError
from .coordinator import ComputersConfigEntry, ComputersCoordinator
from .entity import ComputerEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ComputersConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the notify entity."""
    async_add_entities([ComputerNotify(entry.runtime_data)])


class ComputerNotify(ComputerEntity, NotifyEntity):
    """Shows a native notification on the computer's desktop."""

    _attr_translation_key = "desktop"
    _attr_supported_features = NotifyEntityFeature.TITLE

    def __init__(self, coordinator: ComputersCoordinator) -> None:
        super().__init__(coordinator, "notify")

    @property
    def available(self) -> bool:
        capabilities = self.coordinator.layout.get("capabilities") or {}
        return super().available and bool(capabilities.get("notify"))

    async def async_send_message(self, message: str, title: str | None = None) -> None:
        try:
            await self.coordinator.client.notify(message, title)
        except AgentError as err:
            raise HomeAssistantError(
                f"Notification to {self.coordinator.config_entry.title} failed: {err}"
            ) from err
