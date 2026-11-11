"""Data update coordinator for ASUSTOR NAS."""

from __future__ import annotations

import asyncio
from datetime import timedelta
import logging
import re
import time
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from . import const as c
from .snmp import AsustorSnmp, SnmpAuthError, SnmpError, to_int, to_str, to_text

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
            update_interval=timedelta(
                seconds=entry.options.get(c.CONF_SCAN_INTERVAL, c.DEFAULT_SCAN_INTERVAL)
            ),
        )
        self.client = client
        # None = default rule (eth* always, bond* when up); else explicit interface names.
        self.interface_filter: list[str] | None = entry.options.get(c.CONF_INTERFACES)
        # previous byte counters per interface, to derive throughput: {name: (t, rx, tx)}
        self._prev_counters: dict[str, tuple[float, int | None, int | None]] = {}
        self.model: str | None = None
        self.sw_version: str | None = None
        self.hw_version: str | None = None
        self.serial: str | None = None
        self.last_poll_duration: float | None = None  # seconds, for diagnostics

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            return await self._fetch()
        except SnmpAuthError as err:
            raise ConfigEntryAuthFailed(f"SNMPv3 credentials rejected: {err}") from err
        except SnmpError as err:
            raise UpdateFailed(f"SNMP error: {err}") from err
        except Exception as err:  # noqa: BLE001
            raise UpdateFailed(f"Cannot reach NAS: {err}") from err

    async def async_list_interfaces(self) -> list[str]:
        """Physical-looking interface names offered in the options flow."""
        names = [to_str(v) for v in (await self._safe_walk(c.OID_IF_DESCR)).values()]
        skip = ("lo", "veth", "br-", "docker", "virbr", "tun", "tap", "sit", "ip6", "dummy")
        return [n for n in names if not n.startswith(skip)]

    async def _safe_walk(self, oid: str) -> dict[str, Any]:
        """Walk a subtree; unsupported/empty subtrees (or errors) yield {}."""
        try:
            return await self.client.walk(oid)
        except Exception:  # noqa: BLE001
            return {}

    async def _fetch(self) -> dict[str, Any]:
        started = time.monotonic()
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

        # Independent groups are fetched concurrently; the client caps requests in flight.
        keys = ("cpus", "fans", "temps", "networks", "volumes", "interfaces", "disks", "diskio", "ups")
        results = await asyncio.gather(
            self._cpus(), self._fans(), self._temps(), self._networks(), self._volumes(),
            self._interfaces(), self._disks(), self._diskio(), self._ups(),
            return_exceptions=True,
        )
        for res in results:
            if isinstance(res, BaseException):
                raise res
        data = dict(zip(keys, results, strict=True))
        data["interfaces"] = self._with_rates(data["interfaces"])
        data["system"] = system
        self.last_poll_duration = round(time.monotonic() - started, 2)
        _LOGGER.debug("ASUSTOR poll took %.2f s", self.last_poll_duration)
        return data

    async def _cpus(self) -> dict[str, dict[str, Any]]:
        walked = await self._safe_walk(c.OID_AS_CPU_USAGE)
        return {k: {"usage": to_int(v)} for k, v in walked.items()}

    async def _fans(self) -> dict[str, dict[str, Any]]:
        walked = await self._safe_walk(c.OID_AS_FAN_TABLE)
        return {k: {"speed": to_int(v)} for k, v in walked.items()}

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
            if self.interface_filter is not None:
                if name not in self.interface_filter:
                    continue
            elif not (name.startswith("eth") or (name.startswith("bond") and up)):
                continue
            out[name] = {  # keyed by interface name (stable)
                "name": name,
                "link": "up" if up else "down",
                "speed": to_int(speed.get(idx)),
                "rx": to_int(rx.get(idx)),
                "tx": to_int(tx.get(idx)),
            }
        return out

    def _with_rates(self, rows: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
        """Add rx_rate / tx_rate (Mbit/s) from the counter delta since the previous poll."""
        now = time.monotonic()
        for name, row in rows.items():
            rx, tx = row.get("rx"), row.get("tx")
            row["rx_rate"] = row["tx_rate"] = None
            prev = self._prev_counters.get(name)
            self._prev_counters[name] = (now, rx, tx)
            if prev is None or now <= prev[0]:
                continue
            dt = now - prev[0]
            for key, cur, old in (("rx_rate", rx, prev[1]), ("tx_rate", tx, prev[2])):
                # a negative delta means the counter wrapped or the NAS rebooted: skip one sample
                if cur is not None and old is not None and cur >= old:
                    row[key] = round((cur - old) * 8 / 1e6 / dt, 3)
        for gone in set(self._prev_counters) - set(rows):
            del self._prev_counters[gone]
        return rows

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
