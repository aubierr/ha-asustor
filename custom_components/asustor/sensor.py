"""Sensors for ASUSTOR NAS, grouped by device (NAS, disks, volumes, interfaces, UPS)."""

from __future__ import annotations

from collections.abc import Iterator
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
    EntityCategory,
    UnitOfElectricPotential,
    UnitOfInformation,
    UnitOfTemperature,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN  # noqa: F401
from .coordinator import AsustorCoordinator
from .device import device_info

MEAS = SensorStateClass.MEASUREMENT
TOTAL = SensorStateClass.TOTAL_INCREASING
DIAG = EntityCategory.DIAGNOSTIC
TEMP = dict(
    device_class=SensorDeviceClass.TEMPERATURE,
    native_unit_of_measurement=UnitOfTemperature.CELSIUS,
    state_class=MEAS,
)
DATA_TB = dict(
    device_class=SensorDeviceClass.DATA_SIZE,
    native_unit_of_measurement=UnitOfInformation.TERABYTES,
)
BYTES_GB = dict(
    native_unit_of_measurement=UnitOfInformation.BYTES,
    device_class=SensorDeviceClass.DATA_SIZE,
    suggested_unit_of_measurement=UnitOfInformation.GIGABYTES,
    state_class=TOTAL,
)


def D(key: str, **kw: Any) -> SensorEntityDescription:
    return SensorEntityDescription(key=key, **kw)


def _pct(key: str, **kw: Any) -> SensorEntityDescription:
    return D(key, native_unit_of_measurement=PERCENTAGE, state_class=MEAS, **kw)


SYSTEM_SENSORS: tuple[SensorEntityDescription, ...] = (
    _pct("cpu_usage", translation_key="cpu_usage", icon="mdi:cpu-64-bit"),
    _pct("memory_usage", translation_key="memory_usage", icon="mdi:memory"),
    D("memory_total", translation_key="memory_total", device_class=SensorDeviceClass.DATA_SIZE,
      native_unit_of_measurement=UnitOfInformation.GIBIBYTES, state_class=MEAS,
      entity_category=DIAG, icon="mdi:memory"),
    D("memory_available", translation_key="memory_available", device_class=SensorDeviceClass.DATA_SIZE,
      native_unit_of_measurement=UnitOfInformation.GIBIBYTES, state_class=MEAS, icon="mdi:memory"),
    _pct("swap_usage", translation_key="swap_usage", icon="mdi:swap-horizontal"),
    D("load_1", translation_key="load_1", state_class=MEAS, icon="mdi:gauge"),
    D("load_5", translation_key="load_5", state_class=MEAS, icon="mdi:gauge", entity_registry_enabled_default=False),
    D("load_15", translation_key="load_15", state_class=MEAS, icon="mdi:gauge", entity_registry_enabled_default=False),
    D("uptime", translation_key="uptime", device_class=SensorDeviceClass.DURATION,
      native_unit_of_measurement=UnitOfTime.SECONDS, suggested_unit_of_measurement=UnitOfTime.DAYS,
      state_class=TOTAL),
    D("timezone", translation_key="timezone", entity_category=DIAG, entity_registry_enabled_default=False),
    D("adm_uptime", translation_key="adm_uptime", entity_category=DIAG, entity_registry_enabled_default=False),
    D("system_time", translation_key="system_time", entity_category=DIAG, entity_registry_enabled_default=False),
    D("cpu_model", translation_key="cpu_model", entity_category=DIAG, icon="mdi:cpu-64-bit"),
)


def _specs(data: dict[str, Any]) -> Iterator[tuple[str, str, str | None, SensorEntityDescription, tuple[str, ...]]]:
    """Yield (unique_id, group, index, description, extra_attribute_keys) for every available value.

    Entity names are relative to their device (the device name is prefixed by Home Assistant).
    """

    def has(group: str, idx: str | None, key: str) -> bool:
        row = data[group] if idx is None else data[group].get(idx, {})
        return row.get(key) is not None

    # --- NAS device -------------------------------------------------------
    for d in SYSTEM_SENSORS:
        if has("system", None, d.key):
            yield d.key, "system", None, d, ()

    for idx in data["cpus"]:
        yield f"cpu{idx}", "cpus", idx, _pct("usage", name=f"CPU core {idx} usage", icon="mdi:cpu-64-bit"), ()

    multi_fan = len(data["fans"]) > 1
    for idx in data["fans"]:
        n = f"Fan {idx} speed" if multi_fan else "Fan speed"
        yield f"fan{idx}", "fans", idx, D("speed", name=n, native_unit_of_measurement=REVOLUTIONS_PER_MINUTE,
                                          state_class=MEAS, icon="mdi:fan"), ()

    multi_t = len(data["temps"]) > 1
    for idx in data["temps"]:
        sfx = f" {idx}" if multi_t else ""
        for key, label in (("cpu", "CPU"), ("system", "System")):
            if has("temps", idx, key):
                yield f"temp{idx}_{key}", "temps", idx, D(key, name=f"{label} temperature{sfx}", **TEMP), ()

    for idx, net in data["networks"].items():
        n = net["name"] or f"Network {idx}"
        for key, label, enabled in (("ipv4", "IPv4 address", True), ("ipv6", "IPv6 address", False),
                                    ("mac", "MAC address", False)):
            if has("networks", idx, key):
                yield f"net{idx}_{key}", "networks", idx, D(
                    key, name=f"{n} {label}", entity_category=DIAG, entity_registry_enabled_default=enabled,
                    icon="mdi:ip-network"), ()
        for key, label in (("sent", "sent (ADM counter)"), ("received", "received (ADM counter)")):
            if has("networks", idx, key):
                yield f"net{idx}_{key}", "networks", idx, D(
                    key, name=f"{n} {label}", native_unit_of_measurement=UnitOfInformation.KILOBYTES,
                    device_class=SensorDeviceClass.DATA_SIZE, state_class=TOTAL,
                    entity_registry_enabled_default=False, icon="mdi:ethernet"), ()

    for idx, io in data["diskio"].items():
        for key, label in (("read", "read"), ("write", "written")):
            if io.get(key) is not None:
                yield f"io{idx}_{key}", "diskio", idx, D(
                    key, name=f"{io['name']} {label}", icon="mdi:harddisk", **BYTES_GB), ()

    # --- one device per volume --------------------------------------------
    for idx, vol in data["volumes"].items():
        yield f"vol{idx}_usage", "volumes", idx, _pct("usage", name="Usage", icon="mdi:harddisk"), (
            "level", "filesystem")
        for key, label, enabled in (("free", "Free space", True), ("used", "Used space", False),
                                    ("total", "Total size", False)):
            yield f"vol{idx}_{key}", "volumes", idx, D(
                key, name=label, state_class=MEAS, entity_registry_enabled_default=enabled,
                icon="mdi:harddisk", **DATA_TB), ()
        for key, label in (("status", "Status"), ("level", "RAID level"), ("filesystem", "File system")):
            if has("volumes", idx, key):
                yield f"vol{idx}_{key}", "volumes", idx, D(
                    key, name=label, entity_category=DIAG if key != "status" else None,
                    icon="mdi:harddisk"), ()

    # --- one device per network interface -----------------------------------
    for idx, itf in data["interfaces"].items():
        for key, label in (("rx", "Received"), ("tx", "Sent")):
            if itf.get(key) is not None:
                yield f"if{idx}_{key}", "interfaces", idx, D(key, name=label, icon="mdi:ethernet", **BYTES_GB), ()
        yield f"if{idx}_link", "interfaces", idx, D("link", name="Link", icon="mdi:ethernet"), ()
        if itf.get("speed") is not None:
            yield f"if{idx}_speed", "interfaces", idx, D(
                "speed", name="Speed", native_unit_of_measurement="Mbit/s", state_class=MEAS,
                entity_category=DIAG, icon="mdi:speedometer"), ()

    # --- one device per disk ------------------------------------------------
    for idx, disk in data["disks"].items():
        if disk.get("temperature") is not None:
            yield f"disk{idx}_temp", "disks", idx, D("temperature", name="Temperature", **TEMP), ("type",)
        for key, label in (("health", "S.M.A.R.T. health"), ("status", "Status")):
            if disk.get(key) is not None:
                yield f"disk{idx}_{key}", "disks", idx, D(key, name=label, icon="mdi:harddisk"), ()
        if disk.get("size") is not None:
            yield f"disk{idx}_size", "disks", idx, D(
                "size", name="Size", entity_category=DIAG, entity_registry_enabled_default=False,
                icon="mdi:harddisk", **DATA_TB), ()

    # --- one device per UPS -------------------------------------------------
    for idx, ups in data["ups"].items():
        if "status" in ups:
            yield f"ups{idx}_status", "ups", idx, D("status", name="Status", icon="mdi:battery-heart"), ()
        for key, label, kw in (
            ("battery_charge", "Battery charge", dict(native_unit_of_measurement=PERCENTAGE,
                                                       device_class=SensorDeviceClass.BATTERY)),
            ("battery_low", "Low battery threshold", dict(native_unit_of_measurement=PERCENTAGE,
                                                           entity_category=DIAG)),
            ("battery_remaining", "Battery runtime", dict(native_unit_of_measurement=UnitOfTime.SECONDS,
                                                           device_class=SensorDeviceClass.DURATION)),
            ("input_voltage", "Input voltage", dict(native_unit_of_measurement=UnitOfElectricPotential.VOLT,
                                                     device_class=SensorDeviceClass.VOLTAGE)),
        ):
            if key in ups:
                yield f"ups{idx}_{key}", "ups", idx, D(key, name=label, state_class=MEAS, **kw), ()
        if "input_sensitivity" in ups:
            yield f"ups{idx}_sens", "ups", idx, D(
                "input_sensitivity", name="Input sensitivity", entity_category=DIAG), ()
        for key, label in (("vendor_id", "Vendor ID"), ("product_id", "Product ID")):
            if key in ups:
                yield f"ups{idx}_{key}", "ups", idx, D(
                    key, name=label, entity_category=DIAG, entity_registry_enabled_default=False), ()


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: AsustorCoordinator = entry.runtime_data
    known: set[str] = set()

    @callback
    def _discover() -> None:
        new = [
            NasSensor(coordinator, entry, desc, group, idx, uid, attrs)
            for uid, group, idx, desc, attrs in _specs(coordinator.data)
            if uid not in known
        ]
        known.update(e.unique_id_suffix for e in new)
        if new:
            async_add_entities(new)

    _discover()
    entry.async_on_unload(coordinator.async_add_listener(_discover))


class NasSensor(CoordinatorEntity[AsustorCoordinator], SensorEntity):
    """A sensor reading one value from the coordinator data."""

    _attr_has_entity_name = True

    def __init__(self, coordinator, entry, description, group, idx, uid, attrs=()) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._group, self._idx, self._attrs = group, idx, attrs
        self.unique_id_suffix = uid
        self._attr_unique_id = f"{entry.entry_id}_{uid}"
        self._attr_device_info = device_info(coordinator, entry, group, idx)

    def _row(self) -> dict[str, Any]:
        data = self.coordinator.data[self._group]
        return data if self._idx is None else data.get(self._idx, {})

    @property
    def native_value(self) -> Any:
        return self._row().get(self.entity_description.key)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        row = self._row()
        attrs = {k: row[k] for k in self._attrs if row.get(k) is not None}
        return attrs or None
