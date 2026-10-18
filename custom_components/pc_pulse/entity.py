"""Base entity for the PC Pulse integration."""

from __future__ import annotations

from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .coordinator import ComputersCoordinator


class ComputerEntity(CoordinatorEntity[ComputersCoordinator]):
    """Entity bound to one computer."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: ComputersCoordinator, key: str) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.agent_id}_{key}"
        self._attr_device_info = coordinator.device_info

    @property
    def available(self) -> bool:
        return super().available and self.coordinator.data is not None
