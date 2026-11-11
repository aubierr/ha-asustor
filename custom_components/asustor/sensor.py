"""Sensors for ASUSTOR NAS, grouped by device (NAS, disks, volumes, interfaces, UPS).

Every entity name comes from translations (``translation_key``); names that embed a
variable part (CPU core number, interface name...) use ``translation_placeholders``.
"""

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
    UnitOfDataRate,
    UnitOfElectricPotential,
    UnitOfInformation,
    UnitOfTemperature,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

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
RATE = dict(
    device_class=SensorDeviceClass.DATA_RATE,
    native_unit_of_measurement=UnitOfDataRate.MEGABITS_PER_SECOND,
    suggested_display_precision=2,
    state_class=MEAS,
)

Spec = tuple[str, str, "str | None", SensorEntityDescription, tuple[str, ...], dict[str, str]]


def D(key: str, **kw: Any) -> SensorEntityDescription:
    """Description whose translation key defaults to its key."""
    kw.setdefault("translation_key", key)
    return SensorEntityDescription(key=key, **kw)


def _pct(key: str, **kw: Any) -> SensorEntityDescription:
    return D(key, native_unit_of_measurement=PERCENTAGE, state_class=MEAS, **kw)


SYSTEM_SENSORS: tuple[SensorEntityDescription, ...] = (
    _pct("cpu_usage", icon="mdi:cpu-64-bit"),
    _pct("memory_usage", icon="mdi:memory"),
    D("memory_total", device_class=SensorDeviceClass.DATA_SIZE,
      native_unit_of_measurement=UnitOfInformation.GIBIBYTES, state_class=MEAS,
      entity_category=DIAG, icon="mdi:memory"),
    D("memory_available", device_class=SensorDeviceClass.DATA_SIZE,
      native_unit_of_measurement=UnitOfInformation.GIBIBYTES, state_class=MEAS, icon="mdi:memory"),
    _pct("swap_usage", icon="mdi:swap-horizontal"),
    D("load_1", state_class=MEAS, icon="mdi:gauge"),
    D("load_5", state_class=MEAS, icon="mdi:gauge", entity_registry_enabled_default=False),
    D("load_15", state_class=MEAS, icon="mdi:gauge", entity_registry_enabled_default=False),
    D("uptime", device_class=SensorDeviceClass.DURATION,
      native_unit_of_measurement=UnitOfTime.SECONDS, suggested_unit_of_measurement=UnitOfTime.DAYS,
      state_class=TOTAL),
    D("timezone", entity_category=DIAG, entity_registry_enabled_default=False),
    D("adm_uptime", entity_category=DIAG, entity_registry_enabled_default=False),
    D("system_time", entity_category=DIAG, entity_registry_enabled_default=False),
    D("cpu_model", entity_category=DIAG, icon="mdi:cpu-64-bit"),
)


def _specs(data: dict[str, Any]) -> Iterator[Spec]:
    """Yield (unique_id, group, index, description, extra_attribute_keys, placeholders)."""

    def has(group: str, idx: str | None, key: str) -> bool:
        row = data[group] if idx is None else data[group].get(idx, {})
        return row.get(key) is not None

    # --- NAS device -------------------------------------------------------
    for d in SYSTEM_SENSORS:
        if has("system", None, d.key):
            yield d.key, "system", None, d, (), {}

    for idx in data["cpus"]:
        yield (f"cpu{idx}", "cpus", idx, _pct("usage", translation_key="cpu_core_usage",
                                              icon="mdi:cpu-64-bit"), (), {"core": idx})

    multi_fan = len(data["fans"]) > 1
    for idx in data["fans"]:
        yield (f"fan{idx}", "fans", idx,
               D("speed", translation_key="fan_speed_n" if multi_fan else "fan_speed",
                 native_unit_of_measurement=REVOLUTIONS_PER_MINUTE, state_class=MEAS, icon="mdi:fan"),
               (), {"fan": idx})

    multi_t = len(data["temps"]) > 1
    for idx in data["temps"]:
        for key in ("cpu", "system"):
            if has("temps", idx, key):
                yield (f"temp{idx}_{key}", "temps", idx,
                       D(key, translation_key=f"{key}_temperature" + ("_n" if multi_t else ""), **TEMP),
                       (), {"sensor": idx})

    for idx, net in data["networks"].items():
        ph = {"interface": net["name"] or f"Network {idx}"}
        for key, tk, enabled in (("ipv4", "net_ipv4", True), ("ipv6", "net_ipv6", False),
                                 ("mac", "net_mac", False)):
            if has("networks", idx, key):
                yield (f"net{idx}_{key}", "networks", idx,
                       D(key, translation_key=tk, entity_category=DIAG,
                         entity_registry_enabled_default=enabled, icon="mdi:ip-network"), (), ph)
        for key, tk in (("sent", "net_sent_adm"), ("received", "net_received_adm")):
            if has("networks", idx, key):
                yield (f"net{idx}_{key}", "networks", idx,
                       D(key, translation_key=tk, native_unit_of_measurement=UnitOfInformation.KILOBYTES,
                         device_class=SensorDeviceClass.DATA_SIZE, state_class=TOTAL,
                         entity_registry_enabled_default=False, icon="mdi:ethernet"), (), ph)

    for idx, io in data["diskio"].items():
        for key, tk in (("read", "diskio_read"), ("write", "diskio_written")):
            if io.get(key) is not None:
                yield (f"io{idx}_{key}", "diskio", idx,
                       D(key, translation_key=tk, icon="mdi:harddisk", **BYTES_GB), (), {"device": io["name"]})

    # --- one device per volume --------------------------------------------
    for idx in data["volumes"]:
        yield (f"vol{idx}_usage", "volumes", idx,
               _pct("usage", translation_key="volume_usage", icon="mdi:harddisk"),
               ("level", "filesystem"), {})
        for key, enabled in (("free", True), ("used", False), ("total", False)):
            yield (f"vol{idx}_{key}", "volumes", idx,
                   D(key, translation_key=f"volume_{key}", state_class=MEAS,
                     entity_registry_enabled_default=enabled, icon="mdi:harddisk", **DATA_TB), (), {})
        for key, tk in (("status", "volume_status"), ("level", "volume_raid_level"),
                        ("filesystem", "volume_filesystem")):
            if has("volumes", idx, key):
                yield (f"vol{idx}_{key}", "volumes", idx,
                       D(key, translation_key=tk, entity_category=DIAG if key != "status" else None,
                         icon="mdi:harddisk"), (), {})

    # --- one device per network interface -----------------------------------
    for idx, itf in data["interfaces"].items():
        for key, tk in (("rx", "if_received"), ("tx", "if_sent")):
            if itf.get(key) is not None:
                yield (f"if{idx}_{key}", "interfaces", idx,
                       D(key, translation_key=tk, icon="mdi:ethernet", **BYTES_GB), (), {})
        for key, tk in (("rx_rate", "if_receive_rate"), ("tx_rate", "if_transmit_rate")):
            if key in itf and (itf.get("rx") is not None):
                yield (f"if{idx}_{key}", "interfaces", idx,
                       D(key, translation_key=tk, icon="mdi:speedometer", **RATE), (), {})
        yield (f"if{idx}_link", "interfaces", idx,
               D("link", translation_key="if_link", entity_category=DIAG, icon="mdi:ethernet"), (), {})
        if itf.get("speed") is not None:
            yield (f"if{idx}_speed", "interfaces", idx,
                   D("speed", translation_key="if_speed", native_unit_of_measurement="Mbit/s",
                     state_class=MEAS, entity_category=DIAG, icon="mdi:speedometer"), (), {})

    # --- one device per disk ------------------------------------------------
    for idx, disk in data["disks"].items():
        if disk.get("temperature") is not None:
            yield (f"disk{idx}_temp", "disks", idx,
                   D("temperature", translation_key="disk_temperature", **TEMP), ("type",), {})
        for key, tk in (("health", "disk_health"), ("status", "disk_status")):
            if disk.get(key) is not None:
                yield (f"disk{idx}_{key}", "disks", idx,
                       D(key, translation_key=tk, icon="mdi:harddisk"), (), {})
        if disk.get("size") is not None:
            yield (f"disk{idx}_size", "disks", idx,
                   D("size", translation_key="disk_size", entity_category=DIAG,
                     entity_registry_enabled_default=False, icon="mdi:harddisk", **DATA_TB), (), {})

    # --- one device per UPS -------------------------------------------------
    for idx, ups in data["ups"].items():
        if "status" in ups:
            yield (f"ups{idx}_status", "ups", idx,
                   D("status", translation_key="ups_status", icon="mdi:battery-heart"), (), {})
        for key, tk, kw in (
            ("battery_charge", "ups_battery_charge", dict(native_unit_of_measurement=PERCENTAGE,
                                                          device_class=SensorDeviceClass.BATTERY)),
            ("battery_low", "ups_battery_low", dict(native_unit_of_measurement=PERCENTAGE,
                                                    entity_category=DIAG)),
            ("battery_remaining", "ups_battery_runtime", dict(native_unit_of_measurement=UnitOfTime.SECONDS,
                                                              device_class=SensorDeviceClass.DURATION)),
            ("input_voltage", "ups_input_voltage", dict(native_unit_of_measurement=UnitOfElectricPotential.VOLT,
                                                        device_class=SensorDeviceClass.VOLTAGE)),
        ):
            if key in ups:
                yield (f"ups{idx}_{key}", "ups", idx,
                       D(key, translation_key=tk, state_class=MEAS, **kw), (), {})
        if "input_sensitivity" in ups:
            yield (f"ups{idx}_sens", "ups", idx,
                   D("input_sensitivity", translation_key="ups_input_sensitivity", entity_category=DIAG), (), {})
        for key, tk in (("vendor_id", "ups_vendor_id"), ("product_id", "ups_product_id")):
            if key in ups:
                yield (f"ups{idx}_{key}", "ups", idx,
                       D(key, translation_key=tk, entity_category=DIAG,
                         entity_registry_enabled_default=False), (), {})


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: AsustorCoordinator = entry.runtime_data
    known: set[str] = set()

    @callback
    def _discover() -> None:
        new = [
            NasSensor(coordinator, entry, desc, group, idx, uid, attrs, placeholders)
            for uid, group, idx, desc, attrs, placeholders in _specs(coordinator.data)
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

    def __init__(self, coordinator, entry, description, group, idx, uid, attrs=(), placeholders=None) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._group, self._idx, self._attrs = group, idx, attrs
        self.unique_id_suffix = uid
        self._attr_unique_id = f"{entry.entry_id}_{uid}"
        self._attr_translation_placeholders = placeholders or {}
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
