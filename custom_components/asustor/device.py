"""Device registry helpers: the NAS is the parent of disk/volume/interface/UPS devices."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceInfo

from .const import DOMAIN
from .coordinator import AsustorCoordinator

# coordinator data group -> (device kind used in the identifier, display prefix)
_CHILD_GROUPS = {
    "disks": "disk",
    "volumes": "volume",
    "interfaces": "if",
    "ups": "ups",
}


def device_info(
    coordinator: AsustorCoordinator, entry: ConfigEntry, group: str, idx: str | None
) -> DeviceInfo:
    """Return the device a (group, idx) entity belongs to."""
    kind = _CHILD_GROUPS.get(group)
    if kind is None or idx is None:
        return DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer="ASUSTOR",
            model=coordinator.model,
            sw_version=coordinator.sw_version,
            hw_version=coordinator.hw_version,
            serial_number=coordinator.serial,
        )

    row = coordinator.data[group].get(idx, {})
    info = DeviceInfo(
        identifiers={(DOMAIN, f"{entry.entry_id}_{kind}_{idx}")},
        name=f"{entry.title} {row.get('name', f'{kind} {idx}')}",
    )
    # `via_device` (identifier tuple) is deprecated: link to the parent by registry id.
    registry = dr.async_get(coordinator.hass)
    parent_key = (DOMAIN, entry.entry_id)
    if hasattr(registry, "async_get_device_by_identifier"):  # HA >= 2026.x (async_get_device is deprecated)
        parent = registry.async_get_device_by_identifier(parent_key, entry.entry_id)
    else:
        parent = registry.async_get_device(identifiers={parent_key})
    if parent is not None:
        info["via_device_id"] = parent.id
    if group == "disks":
        info["model"] = row.get("model")
    elif group == "volumes":
        info["manufacturer"] = "ASUSTOR"
        info["model"] = " ".join(x for x in (row.get("level"), row.get("filesystem")) if x) or None
    elif group == "interfaces":
        info["manufacturer"] = "ASUSTOR"
        info["model"] = "Network interface"
    elif group == "ups":
        info["model"] = row.get("name")
    return info


def present_identifiers(entry: ConfigEntry, data: dict) -> set[str]:
    """Identifier strings of every child device currently reported by the NAS."""
    return {
        f"{entry.entry_id}_{kind}_{idx}"
        for group, kind in _CHILD_GROUPS.items()
        for idx in data.get(group, {})
    }


def is_child_identifier(entry: ConfigEntry, ident: str) -> bool:
    return ident != entry.entry_id and ident.startswith(f"{entry.entry_id}_")


# A NAS always has at least one disk and one volume: an empty table means a failed
# read, not a removal, so those groups are never pruned from an empty result.
_NEVER_EMPTY_GROUPS = {"disk", "volume"}
PRUNE_AFTER_MISSES = 3


def prune_stale_devices(hass, entry: ConfigEntry, data: dict, missing: dict[str, int]) -> None:
    """Remove disk/volume/interface/UPS devices that vanished for several polls in a row."""
    present = present_identifiers(entry, data)
    registry = dr.async_get(hass)
    for dev in dr.async_entries_for_config_entry(registry, entry.entry_id):
        for domain, ident in dev.identifiers:
            if domain != DOMAIN or not is_child_identifier(entry, ident):
                continue
            if ident in present:
                missing.pop(ident, None)
                continue
            kind = ident[len(entry.entry_id) + 1 :].split("_", 1)[0]
            group = next((g for g, k in _CHILD_GROUPS.items() if k == kind), None)
            if group is None or (kind in _NEVER_EMPTY_GROUPS and not data.get(group)):
                continue
            missing[ident] = missing.get(ident, 0) + 1
            if missing[ident] >= PRUNE_AFTER_MISSES:
                registry.async_remove_device(dev.id)
                missing.pop(ident, None)
