"""Sensors for the PC Pulse integration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    EntityCategory,
    UnitOfDataRate,
    UnitOfFrequency,
    UnitOfInformation,
    UnitOfTemperature,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from .coordinator import ComputersConfigEntry, ComputersCoordinator
from .entity import ComputerEntity

Data = dict[str, Any]

CPU_TEMP_HINTS = ("cpu", "package", "tctl", "tdie", "core", "k10temp", "coretemp")


def _parse_ts(value: str | None) -> datetime | None:
    return dt_util.parse_datetime(value) if value else None


def _cpu_temperature(data: Data) -> float | None:
    temps = data.get("temperatures") or []
    cpu = [t["celsius"] for t in temps if any(h in t["label"].lower() for h in CPU_TEMP_HINTS)]
    values = cpu or [t["celsius"] for t in temps]
    return max(values) if values else None


def _process_attrs(procs: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "processes": [
            {
                "pid": p["pid"],
                "name": p["name"],
                "cpu_percent": p["cpu_percent"],
                "memory_mb": round(p["memory_bytes"] / 1048576, 1),
            }
            for p in procs
        ]
    }


@dataclass(frozen=True, kw_only=True)
class ComputerSensorDescription(SensorEntityDescription):
    """Describes a computer sensor."""

    value_fn: Callable[[Data], Any]
    attrs_fn: Callable[[Data], dict[str, Any]] | None = None
    exists_fn: Callable[[dict[str, Any]], bool] = lambda layout: True


SENSORS: tuple[ComputerSensorDescription, ...] = (
    ComputerSensorDescription(
        key="cpu_usage",
        translation_key="cpu_usage",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda d: d["cpu"]["usage_percent"],
        attrs_fn=lambda d: {
            "brand": d["cpu"]["brand"],
            "logical_cores": d["cpu"]["logical_cores"],
            "physical_cores": d["cpu"]["physical_cores"],
            "per_core_percent": d["cpu"]["per_core_percent"],
        },
    ),
    ComputerSensorDescription(
        key="cpu_frequency",
        translation_key="cpu_frequency",
        device_class=SensorDeviceClass.FREQUENCY,
        native_unit_of_measurement=UnitOfFrequency.MEGAHERTZ,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
        value_fn=lambda d: d["cpu"]["frequency_mhz"],
    ),
    ComputerSensorDescription(
        key="cpu_temperature",
        translation_key="cpu_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_cpu_temperature,
        attrs_fn=lambda d: {t["label"]: t["celsius"] for t in d.get("temperatures") or []},
        exists_fn=lambda layout: layout.get("has_temperature", False),
    ),
    ComputerSensorDescription(
        key="load_1m",
        translation_key="load_1m",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        value_fn=lambda d: d["cpu"]["load_average"][0],
        attrs_fn=lambda d: {
            "load_5m": d["cpu"]["load_average"][1],
            "load_15m": d["cpu"]["load_average"][2],
        },
        exists_fn=lambda layout: (layout.get("os") or {}).get("family") != "windows",
    ),
    ComputerSensorDescription(
        key="memory_usage",
        translation_key="memory_usage",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda d: d["memory"]["usage_percent"],
        attrs_fn=lambda d: {
            "total_gib": round(d["memory"]["total_bytes"] / 1073741824, 2),
        },
    ),
    ComputerSensorDescription(
        key="memory_used",
        translation_key="memory_used",
        device_class=SensorDeviceClass.DATA_SIZE,
        native_unit_of_measurement=UnitOfInformation.BYTES,
        suggested_unit_of_measurement=UnitOfInformation.GIBIBYTES,
        suggested_display_precision=2,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: d["memory"]["used_bytes"],
    ),
    ComputerSensorDescription(
        key="memory_available",
        translation_key="memory_available",
        device_class=SensorDeviceClass.DATA_SIZE,
        native_unit_of_measurement=UnitOfInformation.BYTES,
        suggested_unit_of_measurement=UnitOfInformation.GIBIBYTES,
        suggested_display_precision=2,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
        value_fn=lambda d: d["memory"]["available_bytes"],
    ),
    ComputerSensorDescription(
        key="swap_usage",
        translation_key="swap_usage",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        entity_registry_enabled_default=False,
        value_fn=lambda d: d["memory"]["swap_usage_percent"],
    ),
    ComputerSensorDescription(
        key="network_download",
        translation_key="network_download",
        device_class=SensorDeviceClass.DATA_RATE,
        native_unit_of_measurement=UnitOfDataRate.BYTES_PER_SECOND,
        suggested_unit_of_measurement=UnitOfDataRate.MEGABITS_PER_SECOND,
        suggested_display_precision=2,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: d["network"]["rx_bytes_per_sec"],
    ),
    ComputerSensorDescription(
        key="network_upload",
        translation_key="network_upload",
        device_class=SensorDeviceClass.DATA_RATE,
        native_unit_of_measurement=UnitOfDataRate.BYTES_PER_SECOND,
        suggested_unit_of_measurement=UnitOfDataRate.MEGABITS_PER_SECOND,
        suggested_display_precision=2,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: d["network"]["tx_bytes_per_sec"],
    ),
    ComputerSensorDescription(
        key="network_received",
        translation_key="network_received",
        device_class=SensorDeviceClass.DATA_SIZE,
        native_unit_of_measurement=UnitOfInformation.BYTES,
        suggested_unit_of_measurement=UnitOfInformation.GIBIBYTES,
        suggested_display_precision=2,
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_registry_enabled_default=False,
        value_fn=lambda d: d["network"]["rx_bytes_total"],
    ),
    ComputerSensorDescription(
        key="network_sent",
        translation_key="network_sent",
        device_class=SensorDeviceClass.DATA_SIZE,
        native_unit_of_measurement=UnitOfInformation.BYTES,
        suggested_unit_of_measurement=UnitOfInformation.GIBIBYTES,
        suggested_display_precision=2,
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_registry_enabled_default=False,
        value_fn=lambda d: d["network"]["tx_bytes_total"],
    ),
    ComputerSensorDescription(
        key="process_count",
        translation_key="process_count",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: d["process_count"],
    ),
    ComputerSensorDescription(
        key="top_process_cpu",
        translation_key="top_process_cpu",
        value_fn=lambda d: d["top_cpu"][0]["name"] if d.get("top_cpu") else None,
        attrs_fn=lambda d: _process_attrs(d.get("top_cpu") or []),
    ),
    ComputerSensorDescription(
        key="top_process_memory",
        translation_key="top_process_memory",
        value_fn=lambda d: d["top_memory"][0]["name"] if d.get("top_memory") else None,
        attrs_fn=lambda d: _process_attrs(d.get("top_memory") or []),
    ),
    ComputerSensorDescription(
        key="idle_time",
        translation_key="idle_time",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: d["activity"]["idle_seconds"],
    ),
    ComputerSensorDescription(
        key="last_active",
        translation_key="last_active",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda d: _parse_ts(d["activity"]["last_active"]),
    ),
    ComputerSensorDescription(
        key="users",
        translation_key="users",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: len(d["activity"]["users"]),
        attrs_fn=lambda d: {"users": d["activity"]["users"]},
    ),
    ComputerSensorDescription(
        key="last_boot",
        translation_key="last_boot",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: _parse_ts(d["boot_time"]),
    ),
    ComputerSensorDescription(
        key="os",
        translation_key="os",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d["os"]["long_version"] or d["os"]["name"],
        attrs_fn=lambda d: {
            "version": d["os"]["version"],
            "kernel": d["os"]["kernel"],
            "arch": d["os"]["arch"],
            "hostname": d["hostname"],
        },
    ),
    ComputerSensorDescription(
        key="ip_address",
        translation_key="ip_address",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.get("primary_ip"),
        attrs_fn=lambda d: {
            "mac_address": d.get("primary_mac"),
            "interfaces": {
                i["name"]: i["ip_addresses"] for i in d.get("interfaces") or [] if i["ip_addresses"]
            },
        },
    ),
    ComputerSensorDescription(
        key="agent_version",
        translation_key="agent_version",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda d: d["agent_version"],
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ComputersConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up sensors."""
    coordinator = entry.runtime_data
    layout = coordinator.layout
    entities: list[SensorEntity] = [
        ComputerSensor(coordinator, desc) for desc in SENSORS if desc.exists_fn(layout)
    ]
    known_disks: set[str] = set(layout.get("disks", []))
    entities.extend(DiskSensor(coordinator, mount) for mount in known_disks)
    async_add_entities(entities)

    @callback
    def _add_new_disks() -> None:
        new = set(coordinator.layout.get("disks", [])) - known_disks
        if new:
            known_disks.update(new)
            async_add_entities(DiskSensor(coordinator, mount) for mount in new)

    entry.async_on_unload(coordinator.async_add_listener(_add_new_disks))


class ComputerSensor(ComputerEntity, SensorEntity):
    """A sensor computed from the status snapshot."""

    entity_description: ComputerSensorDescription

    def __init__(
        self, coordinator: ComputersCoordinator, description: ComputerSensorDescription
    ) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> Any:
        try:
            return self.entity_description.value_fn(self.coordinator.data)
        except (KeyError, IndexError, TypeError):
            return None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if self.entity_description.attrs_fn is None or self.coordinator.data is None:
            return None
        try:
            return self.entity_description.attrs_fn(self.coordinator.data)
        except (KeyError, IndexError, TypeError):
            return None


def _slug(mount: str) -> str:
    slug = "".join(c.lower() if c.isalnum() else "_" for c in mount).strip("_")
    return slug or "root"


class DiskSensor(ComputerEntity, SensorEntity):
    """Disk usage for one mount point."""

    _attr_translation_key = "disk_usage"
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 1

    def __init__(self, coordinator: ComputersCoordinator, mount: str) -> None:
        super().__init__(coordinator, f"disk_{_slug(mount)}")
        self._mount = mount
        self._attr_translation_placeholders = {"mount": mount.rstrip("\\") or mount}

    def _disk(self) -> dict[str, Any] | None:
        for disk in (self.coordinator.data or {}).get("disks", []):
            if disk["mount_point"] == self._mount:
                return disk
        return None

    @property
    def available(self) -> bool:
        return super().available and self._disk() is not None

    @property
    def native_value(self) -> float | None:
        disk = self._disk()
        return disk["usage_percent"] if disk else None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        disk = self._disk()
        if not disk:
            return None
        gib = 1073741824
        return {
            "mount_point": disk["mount_point"],
            "label": disk["name"],
            "file_system": disk["file_system"],
            "kind": disk["kind"],
            "removable": disk["removable"],
            "total_gib": round(disk["total_bytes"] / gib, 2),
            "used_gib": round(disk["used_bytes"] / gib, 2),
            "free_gib": round(disk["available_bytes"] / gib, 2),
        }
