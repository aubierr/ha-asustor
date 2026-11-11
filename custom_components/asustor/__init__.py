"""ASUSTOR NAS integration (SNMP)."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PORT, Platform
from homeassistant.core import HomeAssistant

from .const import CONF_COMMUNITY
from .coordinator import AsustorCoordinator
from .snmp import AsustorSnmp, create_engine

PLATFORMS = [Platform.SENSOR, Platform.UPDATE]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    engine = await hass.async_add_executor_job(create_engine)  # preloads MIBs (blocking I/O)
    client = AsustorSnmp(
        engine, entry.data[CONF_HOST], entry.data[CONF_PORT], entry.data[CONF_COMMUNITY]
    )
    coordinator = AsustorCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    entry.async_on_unload(engine.close_dispatcher)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
