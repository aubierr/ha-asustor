"""Data update coordinator for ASUSTOR NAS."""

from __future__ import annotations

from datetime import timedelta
import logging
import re
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    HR_STORAGE_FIXED_DISK,
    OID_AS_ADM_VERSION,
    OID_AS_CPU_TEMP,
    OID_AS_DISK_HEALTH,
    OID_AS_DISK_MODEL,
    OID_AS_DISK_TEMP,
    OID_AS_FAN_SPEED,
    OID_AS_MODEL,
    OID_AS_SYS_TEMP,
    OID_AS_VOLUME_STATUS,
    OID_CPU_IDLE,
    OID_HR_STORAGE_DESCR,
    OID_HR_STORAGE_SIZE,
    OID_HR_STORAGE_TYPE,
    OID_HR_STORAGE_UNITS,
    OID_HR_STORAGE_USED,
    OID_IF_DESCR,
    OID_IF_HC_IN,
    OID_IF_HC_OUT,
    OID_LOAD_1,
    OID_LOAD_5,
    OID_LOAD_15,
    OID_MEM_AVAIL,
    OID_MEM_BUFFER,
    OID_MEM_CACHED,
    OID_MEM_TOTAL,
    OID_SYS_DESCR,
    OID_SYS_NAME,
    OID_SYS_UPTIME,
)
from .snmp import AsustorSnmp, SnmpError, to_int, to_str

_LOGGER = logging.getLogger(__name__)


class AsustorCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Polls the NAS over SNMP."""

    def __init__(self, hass: HomeAssistant, entry, client: AsustorSnmp) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(seconds=DEFAULT_SCAN_INTERVAL),
        )
        self.client = client
        self.model: str | None = None
        self.sw_version: str | None = None

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            return await self._fetch()
        except SnmpError as err:
            raise UpdateFailed(f"SNMP error: {err}") from err
        except Exception as err:  # noqa: BLE001
            raise UpdateFailed(f"Cannot reach NAS: {err}") from err

    async def _fetch(self) -> dict[str, Any]:
        raw = await self.client.get(
            [
                OID_SYS_DESCR, OID_SYS_NAME, OID_SYS_UPTIME, OID_CPU_IDLE,
                OID_LOAD_1, OID_LOAD_5, OID_LOAD_15,
                OID_MEM_TOTAL, OID_MEM_AVAIL, OID_MEM_BUFFER, OID_MEM_CACHED,
                OID_AS_MODEL, OID_AS_ADM_VERSION, OID_AS_FAN_SPEED, OID_AS_CPU_TEMP, OID_AS_SYS_TEMP,
            ]
        )
        system: dict[str, Any] = {}
        if OID_AS_MODEL in raw:
            self.model = to_str(raw[OID_AS_MODEL])
        if OID_AS_ADM_VERSION in raw:
            self.sw_version = to_str(raw[OID_AS_ADM_VERSION])
        for key, oid in (
            ("fan_speed", OID_AS_FAN_SPEED),
            ("cpu_temperature", OID_AS_CPU_TEMP),
            ("system_temperature", OID_AS_SYS_TEMP),
        ):
            if (v := to_int(raw.get(oid))) is not None:
                system[key] = v

        if (v := to_int(raw.get(OID_SYS_UPTIME))) is not None:
            system["uptime"] = v // 100
        if (v := to_int(raw.get(OID_CPU_IDLE))) is not None:
            system["cpu_usage"] = max(0, 100 - v)
        for key, oid in (("load_1", OID_LOAD_1), ("load_5", OID_LOAD_5), ("load_15", OID_LOAD_15)):
            if (v := to_int(raw.get(oid))) is not None:
                system[key] = v / 100
        total = to_int(raw.get(OID_MEM_TOTAL))
        avail = to_int(raw.get(OID_MEM_AVAIL))
        if total and avail is not None:
            free = avail + (to_int(raw.get(OID_MEM_BUFFER)) or 0) + (to_int(raw.get(OID_MEM_CACHED)) or 0)
            system["memory_usage"] = round(max(0, 100 * (total - free) / total), 1)

        return {
            "system": system,
            "volumes": await self._volumes(),
            "interfaces": await self._interfaces(),
            "disks": await self._disks(),
        }

    async def _volumes(self) -> dict[str, dict[str, Any]]:
        c = self.client
        types = await c.walk(OID_HR_STORAGE_TYPE)
        descr = await c.walk(OID_HR_STORAGE_DESCR)
        units = await c.walk(OID_HR_STORAGE_UNITS)
        size = await c.walk(OID_HR_STORAGE_SIZE)
        used = await c.walk(OID_HR_STORAGE_USED)
        status = await self._safe_walk(OID_AS_VOLUME_STATUS)
        out: dict[str, dict[str, Any]] = {}
        for idx, t in types.items():
            name = to_str(descr.get(idx, ""))
            m = re.fullmatch(r"/volume([1-9]\d*)", name)  # skip /volume0 (system) and subfolders
            if to_str(t) != HR_STORAGE_FIXED_DISK or not m:
                continue
            u, s, us = to_int(units.get(idx)), to_int(size.get(idx)), to_int(used.get(idx))
            if not (u and s):
                continue
            total_b, used_b = s * u, (us or 0) * u
            out[idx] = {
                "name": f"Volume {m.group(1)}",
                "status": to_str(status[m.group(1)]) if m.group(1) in status else None,
                "total": round(total_b / 1e12, 3),
                "used": round(used_b / 1e12, 3),
                "free": round((total_b - used_b) / 1e12, 3),
                "usage": round(100 * used_b / total_b, 1),
            }
        return out

    async def _interfaces(self) -> dict[str, dict[str, Any]]:
        c = self.client
        descr = await c.walk(OID_IF_DESCR)
        rx = await c.walk(OID_IF_HC_IN)
        tx = await c.walk(OID_IF_HC_OUT)
        out: dict[str, dict[str, Any]] = {}
        for idx, d in descr.items():
            name = to_str(d)
            if not name.startswith(("eth", "bond")):
                continue
            out[idx] = {"name": name, "rx": to_int(rx.get(idx)), "tx": to_int(tx.get(idx))}
        return out

    async def _safe_walk(self, oid: str) -> dict[str, Any]:
        try:
            return await self.client.walk(oid)
        except Exception:  # noqa: BLE001
            return {}

    async def _disks(self) -> dict[str, dict[str, Any]]:
        temps = await self._safe_walk(OID_AS_DISK_TEMP)
        health = await self._safe_walk(OID_AS_DISK_HEALTH)
        models = await self._safe_walk(OID_AS_DISK_MODEL)
        out: dict[str, dict[str, Any]] = {}
        for idx in sorted(set(temps) | set(health), key=lambda x: int(x) if x.isdigit() else 0):
            t = to_int(temps.get(idx))
            out[idx] = {
                "temperature": t if t is not None and 0 < t < 100 else None,
                "health": to_str(health[idx]) if idx in health else None,
                "model": to_str(models[idx]) if idx in models else None,
            }
        return out
