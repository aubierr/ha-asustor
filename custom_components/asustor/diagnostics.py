"""Diagnostics download: coordinator data plus a raw SNMP dump, with private values redacted."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .coordinator import AsustorCoordinator
from .snmp import to_text

# Keys removed everywhere in the dumped structures.
TO_REDACT = {"timezone", "host", "community", "username", "auth_key", "priv_key", "serial", "mac", "ipv4", "ipv6", "system_time", "unique_id", "title"}

ASUSTOR_ROOT = "1.3.6.1.4.1.44738"
# Raw OIDs (prefix match) that hold private data: serial, ASUSTOR ID (email), MAC/IPv4/IPv6.
REDACTED_OIDS = (
    f"{ASUSTOR_ROOT}.1.1.",
    f"{ASUSTOR_ROOT}.1.8.",
    f"{ASUSTOR_ROOT}.1.5.",
    f"{ASUSTOR_ROOT}.1.6.",
    f"{ASUSTOR_ROOT}.3.1.1.3.",
    f"{ASUSTOR_ROOT}.3.1.1.4.",
    f"{ASUSTOR_ROOT}.3.1.1.5.",
)


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: ConfigEntry) -> dict[str, Any]:
    coordinator: AsustorCoordinator = entry.runtime_data

    raw: dict[str, str] = {}
    try:
        for suffix, value in (await coordinator.client.walk(ASUSTOR_ROOT)).items():
            oid = f"{ASUSTOR_ROOT}.{suffix}"
            raw[oid] = "**REDACTED**" if oid.startswith(REDACTED_OIDS) else to_text(value)
    except Exception as err:  # noqa: BLE001
        raw = {"error": str(err)}

    return {
        "entry": async_redact_data(
            {"data": dict(entry.data), "options": dict(entry.options), "title": entry.title}, TO_REDACT
        ),
        "device": {
            "model": coordinator.model,
            "adm_version": coordinator.sw_version,
            "bios_version": coordinator.hw_version,
        },
        "last_update_success": coordinator.last_update_success,
        "last_poll_duration_s": coordinator.last_poll_duration,
        "update_interval_s": coordinator.update_interval.total_seconds() if coordinator.update_interval else None,
        "data": async_redact_data(coordinator.data, TO_REDACT),
        "asustor_mib_walk": raw,
    }
