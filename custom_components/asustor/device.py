"""Device registry helpers: the NAS is the parent of disk/volume/interface/UPS devices."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
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
        via_device=(DOMAIN, entry.entry_id),
    )
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
