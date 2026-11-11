"""Data update coordinator for ASUSTOR NAS."""

from __future__ import annotations

from datetime import timedelta
import logging
import re
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from . import const as c
from .snmp import AsustorSnmp, SnmpError, to_int, to_str, to_text

_LOGGER = logging.getLogger(__name__)

_TEXT_OIDS = {
    "update_status": c.OID_AS_UPDATE,
    "timezone": c.OID_AS_TIMEZONE,
    "adm_uptime": c.OID_AS_UPTIME_TEXT,
    "system_time": c.OID_AS_TIME,
    "cpu_model": c.OID_AS_CPU_MODEL,
}


class AsustorCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Polls the NAS over SNMP."""

    def __init__(self, hass: HomeAssistant, entry, client: AsustorSnmp) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=c.DOMAIN,
            update_interval=timedelta(seconds=c.DEFAULT_SCAN_INTERVAL),
        )
        self.client = client
        self.model: str | None = None
        self.sw_version: str | None = None
        self.hw_version: str | None = None
        self.serial: str | None = None

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            return await self._fetch()
        except SnmpError as err:
            raise UpdateFailed(f"SNMP error: {err}") from err
        except Exception as err:  # noqa: BLE001
            raise UpdateFailed(f"Cannot reach NAS: {err}") from err

    async def _safe_walk(self, oid: str) -> dict[str, Any]:
        """Walk a subtree; unsupported/empty subtrees (or errors) yield {}."""
        try:
            return await self.client.walk(oid)
        except Exception:  # noqa: BLE001
            return {}

    async def _fetch(self) -> dict[str, Any]:
        raw = await self.client.get(
            [
                c.OID_SYS_UPTIME, c.OID_CPU_IDLE,
                c.OID_LOAD_1, c.OID_LOAD_5, c.OID_LOAD_15,
                c.OID_MEM_TOTAL, c.OID_MEM_AVAIL, c.OID_MEM_BUFFER, c.OID_MEM_CACHED,
                c.OID_MEM_SWAP_TOTAL, c.OID_MEM_SWAP_AVAIL,
                c.OID_AS_MODEL, c.OID_AS_ADM_VERSION, c.OID_AS_SERIAL, c.OID_AS_BIOS,
                *_TEXT_OIDS.values(),
            ]
        )
        system: dict[str, Any] = {}
        text = lambda oid: to_text(raw[oid]) if oid in raw else None  # noqa: E731
        self.model = text(c.OID_AS_MODEL)
        self.sw_version = text(c.OID_AS_ADM_VERSION)
        self.hw_version = text(c.OID_AS_BIOS)
        self.serial = text(c.OID_AS_SERIAL)
        for key, oid in _TEXT_OIDS.items():
            if txt := text(oid):
                system[key] = txt

        if (v := to_int(raw.get(c.OID_SYS_UPTIME))) is not None:
            system["uptime"] = v // 100
        if (v := to_int(raw.get(c.OID_CPU_IDLE))) is not None:
            system["cpu_usage"] = max(0, 100 - v)
        for key, oid in (("load_1", c.OID_LOAD_1), ("load_5", c.OID_LOAD_5), ("load_15", c.OID_LOAD_15)):
            if (v := to_int(raw.get(oid))) is not None:
                system[key] = v / 100
        total = to_int(raw.get(c.OID_MEM_TOTAL))
        avail = to_int(raw.get(c.OID_MEM_AVAIL))
        if total and avail is not None:
            free = avail + (to_int(raw.get(c.OID_MEM_BUFFER)) or 0) + (to_int(raw.get(c.OID_MEM_CACHED)) or 0)
            system["memory_total"] = round(total / 1024 / 1024, 2)  # GiB
            system["memory_available"] = round(free / 1024 / 1024, 2)
            system["memory_usage"] = round(max(0, 100 * (total - free) / total), 1)
        swap_t = to_int(raw.get(c.OID_MEM_SWAP_TOTAL))
        swap_a = to_int(raw.get(c.OID_MEM_SWAP_AVAIL))
        if swap_t and swap_a is not None:
            system["swap_usage"] = round(max(0, 100 * (swap_t - swap_a) / swap_t), 1)

        return {
            "system": system,
            "cpus": {k: {"usage": to_int(v)} for k, v in (await self._safe_walk(c.OID_AS_CPU_USAGE)).items()},
            "fans": {k: {"speed": to_int(v)} for k, v in (await self._safe_walk(c.OID_AS_FAN_TABLE)).items()},
            "temps": await self._temps(),
            "networks": await self._networks(),
            "volumes": await self._volumes(),
            "interfaces": await self._interfaces(),
            "disks": await self._disks(),
            "diskio": await self._diskio(),
            "ups": await self._ups(),
        }

    async def _temps(self) -> dict[str, dict[str, Any]]:
        cpu = await self._safe_walk(c.OID_AS_CPU_TEMP_TABLE)
        sys_ = await self._safe_walk(c.OID_AS_SYS_TEMP_TABLE)
        return {
            idx: {"cpu": to_int(cpu.get(idx)), "system": to_int(sys_.get(idx))}
            for idx in sorted(set(cpu) | set(sys_))
        }

    async def _networks(self) -> dict[str, dict[str, Any]]:
        names = await self._safe_walk(c.OID_AS_NET_NAME)
        mac = await self._safe_walk(c.OID_AS_NET_MAC)
        v4 = await self._safe_walk(c.OID_AS_NET_IPV4)
        v6 = await self._safe_walk(c.OID_AS_NET_IPV6)
        sent = await self._safe_walk(c.OID_AS_NET_SENT)
        recv = await self._safe_walk(c.OID_AS_NET_RECEIVED)
        out: dict[str, dict[str, Any]] = {}
        for idx, n in names.items():
            out[idx] = {
                "name": to_text(n),
                "mac": to_text(mac[idx]) if idx in mac else None,
                "ipv4": (to_text(v4[idx]) or None) if idx in v4 else None,
                "ipv6": (to_text(v6[idx]) or None) if idx in v6 else None,
                "sent": to_int(sent.get(idx)),
                "received": to_int(recv.get(idx)),
            }
        return out

    async def _volumes(self) -> dict[str, dict[str, Any]]:
        cl = self.client
        types = await cl.walk(c.OID_HR_STORAGE_TYPE)
        descr = await cl.walk(c.OID_HR_STORAGE_DESCR)
        units = await cl.walk(c.OID_HR_STORAGE_UNITS)
        size = await cl.walk(c.OID_HR_STORAGE_SIZE)
        used = await cl.walk(c.OID_HR_STORAGE_USED)
        status = await self._safe_walk(c.OID_AS_VOLUME_STATUS)
        level = await self._safe_walk(c.OID_AS_VOLUME_LEVEL)
        fs = await self._safe_walk(c.OID_AS_VOLUME_FS)
        out: dict[str, dict[str, Any]] = {}
        for idx, t in types.items():
            name = to_str(descr.get(idx, ""))
            m = re.fullmatch(r"/volume([1-9]\d*)", name)  # skip /volume0 (system) and subfolders
            if to_str(t) != c.HR_STORAGE_FIXED_DISK or not m:
                continue
            u, s, us = to_int(units.get(idx)), to_int(size.get(idx)), to_int(used.get(idx))
            if not (u and s):
                continue
            n = m.group(1)
            total_b, used_b = s * u, (us or 0) * u
            out[n] = {  # keyed by volume number: hrStorage indexes are not stable across reboots
                "name": f"Volume {n}",
                "status": to_text(status[n]) if n in status else None,
                "level": to_text(level[n]) if n in level else None,
                "filesystem": to_text(fs[n]) if n in fs else None,
                "total": round(total_b / 1e12, 3),
                "used": round(used_b / 1e12, 3),
                "free": round((total_b - used_b) / 1e12, 3),
                "usage": round(100 * used_b / total_b, 1),
            }
        return out

    async def _interfaces(self) -> dict[str, dict[str, Any]]:
        cl = self.client
        descr = await cl.walk(c.OID_IF_DESCR)
        rx = await cl.walk(c.OID_IF_HC_IN)
        tx = await cl.walk(c.OID_IF_HC_OUT)
        oper = await self._safe_walk(c.OID_IF_OPER_STATUS)
        speed = await self._safe_walk(c.OID_IF_HIGH_SPEED)
        out: dict[str, dict[str, Any]] = {}
        for idx, d in descr.items():
            name = to_str(d)
            up = to_int(oper.get(idx)) == 1
            if not (name.startswith("eth") or (name.startswith("bond") and up)):
                continue
            out[name] = {  # keyed by interface name (stable)
                "name": name,
                "link": "up" if up else "down",
                "speed": to_int(speed.get(idx)),
                "rx": to_int(rx.get(idx)),
                "tx": to_int(tx.get(idx)),
            }
        return out

    async def _disks(self) -> dict[str, dict[str, Any]]:
        temps = await self._safe_walk(c.OID_AS_DISK_TEMP)
        health = await self._safe_walk(c.OID_AS_DISK_HEALTH)
        smart = await self._safe_walk(c.OID_AS_DISK_SMART)
        models = await self._safe_walk(c.OID_AS_DISK_MODEL)
        names = await self._safe_walk(c.OID_AS_DISK_NAME)
        types = await self._safe_walk(c.OID_AS_DISK_TYPE)
        sizes = await self._safe_walk(c.OID_AS_DISK_SIZE)
        out: dict[str, dict[str, Any]] = {}
        for idx in sorted(set(temps) | set(health) | set(smart), key=lambda x: int(x) if x.isdigit() else 0):
            t = to_int(temps.get(idx))
            gb = to_int(sizes.get(idx))
            out[idx] = {
                "name": to_text(names[idx]) if idx in names else f"Disk {idx}",
                "temperature": t if t is not None and 0 < t < 100 else None,
                "health": to_text(health[idx]) if idx in health else None,  # S.M.A.R.T.
                "status": to_text(smart[idx]) if idx in smart else None,  # Good/Idle/Abnormal
                "size": round(gb / 1000, 3) if gb else None,  # TB
                "model": to_text(models[idx]) if idx in models else None,
                "type": to_text(types[idx]) if idx in types else None,
            }
        return out

    async def _diskio(self) -> dict[str, dict[str, Any]]:
        dev = await self._safe_walk(c.OID_DISKIO_DEVICE)
        rd = await self._safe_walk(c.OID_DISKIO_READ)
        wr = await self._safe_walk(c.OID_DISKIO_WRITE)
        out: dict[str, dict[str, Any]] = {}
        for idx, d in dev.items():
            name = to_text(d)
            if re.fullmatch(r"(sd[a-z]+|nvme\d+n\d+)", name):
                out[idx] = {"name": name, "read": to_int(rd.get(idx)), "write": to_int(wr.get(idx))}
        return out

    async def _ups(self) -> dict[str, dict[str, Any]]:
        cols = {
            key: await self._safe_walk(f"{c.OID_AS_UPS}.{col}") for key, col in c.UPS_COLUMNS.items()
        }
        out: dict[str, dict[str, Any]] = {}
        for idx in cols["manufacturer"].keys() | cols["model"].keys() | cols["status"].keys():
            manu = to_text(cols["manufacturer"].get(idx, ""))
            model = to_text(cols["model"].get(idx, ""))
            if not (manu or model):  # phantom row: no UPS actually connected
                continue
            row: dict[str, Any] = {"name": " ".join(x for x in (manu, model) if x)}
            for key in ("status", "input_sensitivity", "vendor_id", "product_id"):
                if txt := to_text(cols[key].get(idx, "")):
                    row[key] = txt
            for key in ("battery_remaining", "battery_charge", "battery_low", "input_voltage"):
                if (v := to_int(cols[key].get(idx))) is not None:
                    row[key] = v
            out[idx] = row
        return out
