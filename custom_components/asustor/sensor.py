"""Sensors for ASUSTOR NAS."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    PERCENTAGE,
    REVOLUTIONS_PER_MINUTE,
    UnitOfInformation,
    UnitOfTemperature,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import AsustorCoordinator

M = SensorStateClass.MEASUREMENT

SYSTEM_SENSORS: tuple[SensorEntityDescription, ...] = (
    SensorEntityDescription(
        key="cpu_usage", translation_key="cpu_usage",
        native_unit_of_measurement=PERCENTAGE, state_class=M, icon="mdi:cpu-64-bit",
    ),
    SensorEntityDescription(
        key="memory_usage", translation_key="memory_usage",
        native_unit_of_measurement=PERCENTAGE, state_class=M, icon="mdi:memory",
    ),
    SensorEntityDescription(
        key="cpu_temperature", name="CPU temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS, state_class=M,
    ),
    SensorEntityDescription(
        key="system_temperature", name="System temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS, state_class=M,
    ),
    SensorEntityDescription(
        key="fan_speed", name="Fan speed",
        native_unit_of_measurement=REVOLUTIONS_PER_MINUTE, state_class=M, icon="mdi:fan",
    ),
    SensorEntityDescription(
        key="load_1", translation_key="load_1", state_class=M, icon="mdi:gauge"
    ),
    SensorEntityDescription(
        key="load_5", translation_key="load_5", state_class=M, icon="mdi:gauge",
        entity_registry_enabled_default=False,
    ),
    SensorEntityDescription(
        key="load_15", translation_key="load_15", state_class=M, icon="mdi:gauge",
        entity_registry_enabled_default=False,
    ),
    SensorEntityDescription(
        key="uptime", translation_key="uptime",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        suggested_unit_of_measurement=UnitOfTime.DAYS,
        state_class=SensorStateClass.TOTAL_INCREASING,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: AsustorCoordinator = entry.runtime_data
    known: set[str] = set()

    @callback
    def _discover() -> None:
        data = coordinator.data
        new: list[SensorEntity] = []
        for d in SYSTEM_SENSORS:
            if d.key in data["system"] and d.key not in known:
                known.add(d.key)
                new.append(NasSensor(coordinator, entry, d, "system", None, d.key))
        for idx, vol in data["volumes"].items():
            for field, unit, dc in (
                ("usage", PERCENTAGE, None),
                ("free", UnitOfInformation.TERABYTES, SensorDeviceClass.DATA_SIZE),
                ("total", UnitOfInformation.TERABYTES, SensorDeviceClass.DATA_SIZE),
            ):
                uid = f"vol{idx}_{field}"
                if uid in known:
                    continue
                known.add(uid)
                d = SensorEntityDescription(
                    key=field, name=f"{vol['name']} {field}",
                    native_unit_of_measurement=unit, device_class=dc,
                    state_class=M, icon="mdi:harddisk",
                    entity_registry_enabled_default=field != "total",
                )
                new.append(NasSensor(coordinator, entry, d, "volumes", idx, uid))
        for idx, vol in data["volumes"].items():
            uid = f"vol{idx}_status"
            if uid not in known and vol.get("status") is not None:
                known.add(uid)
                d = SensorEntityDescription(key="status", name=f"{vol['name']} status", icon="mdi:harddisk")
                new.append(NasSensor(coordinator, entry, d, "volumes", idx, uid))
        for idx, itf in data["interfaces"].items():
            for field, label in (("rx", "received"), ("tx", "sent")):
                uid = f"if{idx}_{field}"
                if uid in known or itf.get(field) is None:
                    continue
                known.add(uid)
                d = SensorEntityDescription(
                    key=field, name=f"{itf['name']} {label}",
                    native_unit_of_measurement=UnitOfInformation.BYTES,
                    device_class=SensorDeviceClass.DATA_SIZE,
                    suggested_unit_of_measurement=UnitOfInformation.GIGABYTES,
                    state_class=SensorStateClass.TOTAL_INCREASING,
                    icon="mdi:ethernet",
                )
                new.append(NasSensor(coordinator, entry, d, "interfaces", idx, uid))
        for idx, disk in data["disks"].items():
            label = f"Disk {idx}"
            if disk.get("temperature") is not None and f"disk{idx}_temp" not in known:
                known.add(f"disk{idx}_temp")
                d = SensorEntityDescription(
                    key="temperature", name=f"{label} temperature",
                    device_class=SensorDeviceClass.TEMPERATURE,
                    native_unit_of_measurement=UnitOfTemperature.CELSIUS, state_class=M,
                )
                new.append(NasSensor(coordinator, entry, d, "disks", idx, f"disk{idx}_temp"))
            if disk.get("health") is not None and f"disk{idx}_health" not in known:
                known.add(f"disk{idx}_health")
                d = SensorEntityDescription(key="health", name=f"{label} health", icon="mdi:harddisk")
                new.append(NasSensor(coordinator, entry, d, "disks", idx, f"disk{idx}_health"))
        if new:
            async_add_entities(new)

    _discover()
    entry.async_on_unload(coordinator.async_add_listener(_discover))


class NasSensor(CoordinatorEntity[AsustorCoordinator], SensorEntity):
    """A sensor reading one value from the coordinator data."""

    _attr_has_entity_name = True

    def __init__(self, coordinator, entry, description, group, idx, uid) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._group, self._idx = group, idx
        self._attr_unique_id = f"{entry.entry_id}_{uid}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer="ASUSTOR",
            model=coordinator.model,
            sw_version=coordinator.sw_version,
        )

    @property
    def native_value(self) -> Any:
        data = self.coordinator.data[self._group]
        if self._idx is not None:
            data = data.get(self._idx, {})
        return data.get(self.entity_description.key)
