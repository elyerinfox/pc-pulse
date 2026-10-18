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
    UnitOfPower,
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


def _hardware(data: Data) -> Data:
    return data.get("hardware") or {}


def _board(data: Data) -> str | None:
    hw = _hardware(data)
    parts = [hw.get("board_manufacturer"), hw.get("board_product")]
    if not parts[1]:
        parts = [hw.get("system_manufacturer"), hw.get("system_product")]
    return " ".join(p for p in parts if p) or None


def _board_attrs(data: Data) -> dict[str, Any]:
    hw = _hardware(data)
    system = [hw.get("system_manufacturer"), hw.get("system_product")]
    return {
        "system": " ".join(p for p in system if p) or None,
        "bios_vendor": hw.get("bios_vendor"),
        "bios_version": hw.get("bios_version"),
        "bios_date": hw.get("bios_date"),
    }


def _memory_attrs(data: Data) -> dict[str, Any]:
    hw = _hardware(data)
    modules = hw.get("memory_modules") or []
    gib = 1073741824
    return {
        "type": hw.get("memory_type"),
        "module_count": len(modules),
        "installed_gib": round(sum(m["size_bytes"] for m in modules) / gib, 1),
        "modules": [
            {
                "slot": m.get("locator"),
                "size_gib": round(m["size_bytes"] / gib, 1),
                "rated_speed_mts": m.get("speed_mts"),
                "configured_speed_mts": m.get("configured_speed_mts"),
                "manufacturer": m.get("manufacturer"),
                "part_number": m.get("part_number"),
            }
            for m in modules
        ],
    }


def _battery_attrs(data: Data) -> dict[str, Any]:
    battery = data["battery"]
    remaining = battery.get("time_remaining_secs")
    return {
        "plugged_in": battery.get("plugged_in"),
        "time_remaining_min": round(remaining / 60) if remaining is not None else None,
    }


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
        key="memory_speed",
        translation_key="memory_speed",
        native_unit_of_measurement="MT/s",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: _hardware(d).get("memory_speed_mts"),
        attrs_fn=_memory_attrs,
        exists_fn=lambda layout: layout.get("has_memory_speed", False),
    ),
    ComputerSensorDescription(
        key="disk_read",
        translation_key="disk_read",
        device_class=SensorDeviceClass.DATA_RATE,
        native_unit_of_measurement=UnitOfDataRate.BYTES_PER_SECOND,
        suggested_unit_of_measurement=UnitOfDataRate.MEGABYTES_PER_SECOND,
        suggested_display_precision=2,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: d["disk_io"]["read_bytes_per_sec"],
        exists_fn=lambda layout: layout.get("has_disk_io", False),
    ),
    ComputerSensorDescription(
        key="disk_write",
        translation_key="disk_write",
        device_class=SensorDeviceClass.DATA_RATE,
        native_unit_of_measurement=UnitOfDataRate.BYTES_PER_SECOND,
        suggested_unit_of_measurement=UnitOfDataRate.MEGABYTES_PER_SECOND,
        suggested_display_precision=2,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: d["disk_io"]["write_bytes_per_sec"],
        exists_fn=lambda layout: layout.get("has_disk_io", False),
    ),
    ComputerSensorDescription(
        key="battery",
        translation_key="battery",
        device_class=SensorDeviceClass.BATTERY,
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: d["battery"]["percent"],
        attrs_fn=_battery_attrs,
        exists_fn=lambda layout: layout.get("has_battery", False),
    ),
    ComputerSensorDescription(
        key="os_updates",
        translation_key="os_updates",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: d["os_updates"]["count"],
        attrs_fn=lambda d: {
            "updates": d["os_updates"]["titles"],
            "source": d["os_updates"]["source"],
            "last_checked": d["os_updates"]["checked_at"],
        },
        exists_fn=lambda layout: (layout.get("capabilities") or {}).get("os_updates", False),
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
        key="motherboard",
        translation_key="motherboard",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_board,
        attrs_fn=_board_attrs,
        exists_fn=lambda layout: layout.get("has_board", False),
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


@dataclass(frozen=True, kw_only=True)
class GpuSensorDescription(SensorEntityDescription):
    """Describes a per-GPU sensor; `field` is the key in the agent's GPU data."""

    field: str
    attrs_fn: Callable[[Data], dict[str, Any]] | None = None


def _pct(key: str, field: str, enabled: bool = True, **kw: Any) -> GpuSensorDescription:
    return GpuSensorDescription(
        key=key,
        field=field,
        translation_key=f"gpu_{key}",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=enabled,
        **kw,
    )


def _clock(key: str, field: str) -> GpuSensorDescription:
    return GpuSensorDescription(
        key=key,
        field=field,
        translation_key=f"gpu_{key}",
        device_class=SensorDeviceClass.FREQUENCY,
        native_unit_of_measurement=UnitOfFrequency.MEGAHERTZ,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
    )


GPU_SENSORS: tuple[GpuSensorDescription, ...] = (
    _pct("usage", "usage_percent", attrs_fn=lambda g: {"name": g["name"], "vendor": g["vendor"]}),
    _pct(
        "memory_usage",
        "memory_usage_percent",
        suggested_display_precision=1,
        attrs_fn=lambda g: {
            "used_gib": round((g["memory_used_bytes"] or 0) / 1073741824, 2),
            "total_gib": round((g["memory_total_bytes"] or 0) / 1073741824, 2),
        },
    ),
    GpuSensorDescription(
        key="memory_used",
        field="memory_used_bytes",
        translation_key="gpu_memory_used",
        device_class=SensorDeviceClass.DATA_SIZE,
        native_unit_of_measurement=UnitOfInformation.BYTES,
        suggested_unit_of_measurement=UnitOfInformation.GIBIBYTES,
        suggested_display_precision=2,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
    ),
    GpuSensorDescription(
        key="temperature",
        field="temperature_celsius",
        translation_key="gpu_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    GpuSensorDescription(
        key="power",
        field="power_watts",
        translation_key="gpu_power",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.WATT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        attrs_fn=lambda g: {"power_limit_w": g["power_limit_watts"]},
    ),
    _clock("core_clock", "core_clock_mhz"),
    _clock("memory_clock", "memory_clock_mhz"),
    _pct("fan", "fan_percent", enabled=False),
    _pct("encoder", "encoder_percent", enabled=False),
    _pct("decoder", "decoder_percent", enabled=False),
)


def _gpu_entities(
    coordinator: ComputersCoordinator, gpus: list[dict[str, Any]], total: int
) -> list[SensorEntity]:
    return [
        GpuSensor(coordinator, gpu["index"], total, desc)
        for gpu in gpus
        for desc in GPU_SENSORS
        if desc.field in gpu["fields"]
    ]


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
    gpus = layout.get("gpus", [])
    known_gpus: set[int] = {g["index"] for g in gpus}
    entities.extend(_gpu_entities(coordinator, gpus, len(gpus)))
    async_add_entities(entities)

    @callback
    def _add_new_hardware() -> None:
        new = set(coordinator.layout.get("disks", [])) - known_disks
        if new:
            known_disks.update(new)
            async_add_entities(DiskSensor(coordinator, mount) for mount in new)
        current = coordinator.layout.get("gpus", [])
        new_gpus = [g for g in current if g["index"] not in known_gpus]
        if new_gpus:
            known_gpus.update(g["index"] for g in new_gpus)
            async_add_entities(_gpu_entities(coordinator, new_gpus, len(current)))

    entry.async_on_unload(coordinator.async_add_listener(_add_new_hardware))


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


class GpuSensor(ComputerEntity, SensorEntity):
    """One statistic of one GPU."""

    entity_description: GpuSensorDescription

    def __init__(
        self,
        coordinator: ComputersCoordinator,
        index: int,
        total: int,
        description: GpuSensorDescription,
    ) -> None:
        super().__init__(coordinator, f"gpu{index}_{description.key}")
        self.entity_description = description
        self._index = index
        self._attr_translation_placeholders = {
            "gpu": "GPU" if total <= 1 else f"GPU {index + 1}"
        }

    def _gpu(self) -> dict[str, Any] | None:
        for gpu in (self.coordinator.data or {}).get("gpus") or []:
            if gpu["index"] == self._index:
                return gpu
        return None

    @property
    def available(self) -> bool:
        return super().available and self._gpu() is not None

    @property
    def native_value(self) -> Any:
        gpu = self._gpu()
        return gpu.get(self.entity_description.field) if gpu else None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        gpu = self._gpu()
        if gpu is None or self.entity_description.attrs_fn is None:
            return None
        return self.entity_description.attrs_fn(gpu)
