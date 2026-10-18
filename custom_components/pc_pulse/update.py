"""Agent updates, installed by the agent itself from GitHub releases."""

from __future__ import annotations

from typing import Any

from homeassistant.components.update import UpdateEntity, UpdateEntityFeature
from homeassistant.const import EntityCategory
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
    """Set up the update entity."""
    async_add_entities([AgentUpdate(entry.runtime_data)])


class AgentUpdate(ComputerEntity, UpdateEntity):
    """The agent's version, with installs handled by the agent."""

    _attr_translation_key = "agent"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_supported_features = (
        UpdateEntityFeature.INSTALL | UpdateEntityFeature.RELEASE_NOTES
    )

    def __init__(self, coordinator: ComputersCoordinator) -> None:
        super().__init__(coordinator, "agent_update")

    def _update(self) -> dict[str, Any]:
        return (self.coordinator.data or {}).get("update") or {}

    @property
    def available(self) -> bool:
        capabilities = self.coordinator.layout.get("capabilities") or {}
        return super().available and bool(capabilities.get("update"))

    @property
    def installed_version(self) -> str | None:
        return (self.coordinator.data or {}).get("agent_version")

    @property
    def latest_version(self) -> str | None:
        # Before the agent's first check, report the installed version (no update).
        return self._update().get("latest") or self.installed_version

    @property
    def release_url(self) -> str | None:
        return self._update().get("release_url")

    @property
    def in_progress(self) -> bool:
        return bool(self._update().get("installing"))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        update = self._update()
        return {
            "auto_update": update.get("auto"),
            "last_checked": update.get("checked_at"),
            "last_error": update.get("error"),
        }

    async def async_release_notes(self) -> str | None:
        return self._update().get("release_notes")

    async def async_install(self, version: str | None, backup: bool, **kwargs: Any) -> None:
        try:
            await self.coordinator.client.install_update()
        except AgentError as err:
            raise HomeAssistantError(
                f"Updating the agent on {self.coordinator.config_entry.title} failed: {err}"
            ) from err
        await self.coordinator.async_request_refresh()
