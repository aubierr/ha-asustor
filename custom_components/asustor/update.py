"""ADM update entity (read-only: reports whether a newer ADM is available)."""

from __future__ import annotations

from homeassistant.components.update import UpdateDeviceClass, UpdateEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .coordinator import AsustorCoordinator
from .device import device_info


def update_available(status: str | None) -> bool | None:
    """Interpret sysUpgradeAvailable.

    ASUSTOR documents "Available"/"Unavailable"; ADM 5.1 actually returns
    "The ADM is latest version" when up to date.
    """
    if not status:
        return None
    s = status.lower()
    if "latest" in s or "unavailable" in s or "up to date" in s:
        return False
    return True


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    async_add_entities([AdmUpdate(entry.runtime_data, entry)])


class AdmUpdate(CoordinatorEntity[AsustorCoordinator], UpdateEntity):
    """Whether a newer ADM firmware is available (installation is done in ADM)."""

    _attr_has_entity_name = True
    _attr_translation_key = "adm"
    _attr_device_class = UpdateDeviceClass.FIRMWARE
    _attr_title = "ADM"

    def __init__(self, coordinator: AsustorCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_adm_update"
        self._attr_device_info = device_info(coordinator, entry, "system", None)

    @property
    def installed_version(self) -> str | None:
        return self.coordinator.sw_version

    @property
    def latest_version(self) -> str | None:
        status = self.coordinator.data["system"].get("update_status")
        available = update_available(status)
        if available is None:
            return None
        # SNMP only says "available or not", not which version.
        return "newer version" if available else self.coordinator.sw_version

    @property
    def release_summary(self) -> str | None:
        return self.coordinator.data["system"].get("update_status")
