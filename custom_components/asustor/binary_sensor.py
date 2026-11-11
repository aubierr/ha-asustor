"""Problem / state binary sensors for ASUSTOR NAS."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .coordinator import AsustorCoordinator
from .device import device_info

Row = dict[str, Any]

# S.M.A.R.T. states per the ASUSTOR disk MIB: Healthy/Normal vs Risky/Dangerous/Bad.
DISK_SMART_BAD = {"risky", "dangerous", "bad"}
# Volume states: degraded/failed/inactive are faults; resync/reshape/... are transitional.
VOLUME_BAD = {"degraded", "failed", "inactive"}
# NUT-style UPS status flags ("OL", "OB", "OB LB", ...).


def _norm(value: Any) -> str | None:
    return value.strip().lower() if isinstance(value, str) and value.strip() else None


def disk_problem(row: Row) -> bool | None:
    health, status = _norm(row.get("health")), _norm(row.get("status"))
    if health is None and status is None:
        return None
    return health in DISK_SMART_BAD or status == "abnormal"


def volume_problem(row: Row) -> bool | None:
    status = _norm(row.get("status"))
    return None if status is None else status in VOLUME_BAD


def link_up(row: Row) -> bool | None:
    link = _norm(row.get("link"))
    return None if link is None else link == "up"


def _ups_flags(row: Row) -> set[str] | None:
    status = row.get("status")
    return {t.upper() for t in status.replace(",", " ").split()} if isinstance(status, str) and status else None


def ups_on_battery(row: Row) -> bool | None:
    flags = _ups_flags(row)
    return None if flags is None else ("OB" in flags or "DISCHRG" in flags)


def ups_low_battery(row: Row) -> bool | None:
    flags = _ups_flags(row)
    return None if flags is None else "LB" in flags


def _desc(key: str, translation_key: str, **kw: Any) -> BinarySensorEntityDescription:
    return BinarySensorEntityDescription(key=key, translation_key=translation_key, **kw)


def _specs(
    data: dict[str, Any],
) -> Iterator[tuple[str, str, str, BinarySensorEntityDescription, Callable[[Row], bool | None], tuple[str, ...]]]:
    """Yield (unique_id, group, index, description, state_function, attribute_keys)."""
    for idx in data["disks"]:
        yield (f"disk{idx}_problem", "disks", idx,
               _desc("problem", "problem", device_class=BinarySensorDeviceClass.PROBLEM),
               disk_problem, ("health", "status"))
    for idx in data["volumes"]:
        yield (f"vol{idx}_problem", "volumes", idx,
               _desc("problem", "problem", device_class=BinarySensorDeviceClass.PROBLEM),
               volume_problem, ("status", "level"))
    for idx in data["interfaces"]:
        yield (f"if{idx}_connected", "interfaces", idx,
               _desc("connected", "connected", device_class=BinarySensorDeviceClass.CONNECTIVITY),
               link_up, ("speed",))
    for idx in data["ups"]:
        yield (f"ups{idx}_on_battery", "ups", idx,
               _desc("on_battery", "on_battery", icon="mdi:battery-arrow-down"),
               ups_on_battery, ("status",))
        yield (f"ups{idx}_low_battery", "ups", idx,
               _desc("low_battery", "low_battery", device_class=BinarySensorDeviceClass.BATTERY),
               ups_low_battery, ("status",))


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: AsustorCoordinator = entry.runtime_data
    known: set[str] = set()

    @callback
    def _discover() -> None:
        new = [
            NasBinarySensor(coordinator, entry, desc, group, idx, uid, fn, attrs)
            for uid, group, idx, desc, fn, attrs in _specs(coordinator.data)
            if uid not in known
        ]
        known.update(e.unique_id_suffix for e in new)
        if new:
            async_add_entities(new)

    _discover()
    entry.async_on_unload(coordinator.async_add_listener(_discover))


class NasBinarySensor(CoordinatorEntity[AsustorCoordinator], BinarySensorEntity):
    """A binary sensor derived from one row of coordinator data."""

    _attr_has_entity_name = True

    def __init__(self, coordinator, entry, description, group, idx, uid, fn, attrs=()) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._group, self._idx, self._fn, self._attrs = group, idx, fn, attrs
        self.unique_id_suffix = uid
        self._attr_unique_id = f"{entry.entry_id}_{uid}"
        self._attr_device_info = device_info(coordinator, entry, group, idx)

    def _row(self) -> Row:
        return self.coordinator.data[self._group].get(self._idx, {})

    @property
    def is_on(self) -> bool | None:
        return self._fn(self._row())

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        row = self._row()
        attrs = {k: row[k] for k in self._attrs if row.get(k) is not None}
        return attrs or None
