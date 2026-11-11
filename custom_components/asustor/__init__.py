"""ASUSTOR NAS integration (SNMP v2c / v3)."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PORT, Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceEntry

from .const import DOMAIN
from .coordinator import AsustorCoordinator
from .device import is_child_identifier, present_identifiers, prune_stale_devices
from .snmp import AsustorSnmp, build_auth, create_engine

PLATFORMS = [Platform.BINARY_SENSOR, Platform.SENSOR, Platform.UPDATE]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    engine = await hass.async_add_executor_job(create_engine)  # preloads MIBs (blocking I/O)
    client = AsustorSnmp(engine, entry.data[CONF_HOST], entry.data[CONF_PORT], build_auth(entry.data))
    coordinator = AsustorCoordinator(hass, entry, client)
    try:
        await coordinator.async_config_entry_first_refresh()
    except BaseException:
        engine.close_dispatcher()  # don't leak the engine when setup fails (NAS offline, bad auth...)
        raise
    entry.runtime_data = coordinator
    entry.async_on_unload(engine.close_dispatcher)

    # Create the NAS (parent) device first so disk/volume/interface/UPS devices can link to it.
    dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, entry.entry_id)},
        name=entry.title,
        manufacturer="ASUSTOR",
        model=coordinator.model,
        sw_version=coordinator.sw_version,
        hw_version=coordinator.hw_version,
        serial_number=coordinator.serial,
    )

    # Drop devices of removed disks / volumes / interfaces / UPS after a few absent polls.
    missing: dict[str, int] = {}

    @callback
    def _prune() -> None:
        prune_stale_devices(hass, entry, coordinator.data, missing)

    entry.async_on_unload(coordinator.async_add_listener(_prune))
    _prune()

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_config_entry_device(
    hass: HomeAssistant, entry: ConfigEntry, device_entry: DeviceEntry
) -> bool:
    """Allow deleting a child device from the UI only if the NAS no longer reports it."""
    coordinator: AsustorCoordinator = entry.runtime_data
    present = present_identifiers(entry, coordinator.data)
    for domain, ident in device_entry.identifiers:
        if domain == DOMAIN and (not is_child_identifier(entry, ident) or ident in present):
            return False
    return True
