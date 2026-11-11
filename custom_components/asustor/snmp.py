"""Thin async SNMP client built on pysnmp (v7 API)."""

from __future__ import annotations

from typing import Any

from pysnmp.hlapi.v3arch.asyncio import (
    CommunityData,
    ContextData,
    ObjectIdentity,
    ObjectType,
    SnmpEngine,
    UdpTransportTarget,
    get_cmd,
    walk_cmd,
)


def create_engine() -> SnmpEngine:
    """Create an SnmpEngine with all bundled MIB modules preloaded.

    pysnmp otherwise loads MIB files lazily on the first request, i.e. inside
    the event loop. Call this from an executor thread.
    """
    engine = SnmpEngine()
    engine.get_mib_builder().load_modules()
    return engine


class SnmpError(Exception):
    """SNMP request failed."""


class AsustorSnmp:
    """SNMP v2c client for one NAS."""

    def __init__(self, engine: SnmpEngine, host: str, port: int, community: str) -> None:
        self._engine = engine
        self._host = host
        self._port = port
        self._auth = CommunityData(community, mpModel=1)
        self._target: UdpTransportTarget | None = None
        self._ctx = ContextData()

    async def _transport(self) -> UdpTransportTarget:
        if self._target is None:
            self._target = await UdpTransportTarget.create(
                (self._host, self._port), timeout=5, retries=1
            )
        return self._target

    async def get(self, oids: list[str]) -> dict[str, Any]:
        """GET several scalar OIDs. Missing OIDs are omitted."""
        err_ind, err_stat, _idx, var_binds = await get_cmd(
            self._engine,
            self._auth,
            await self._transport(),
            self._ctx,
            *[ObjectType(ObjectIdentity(o)) for o in oids],
        )
        if err_ind:
            raise SnmpError(str(err_ind))
        if err_stat:
            raise SnmpError(err_stat.prettyPrint())
        out: dict[str, Any] = {}
        for oid, val in var_binds:
            if val.__class__.__name__ in ("NoSuchObject", "NoSuchInstance", "EndOfMibView"):
                continue
            out[str(oid)] = val
        return out

    async def walk(self, base: str) -> dict[str, Any]:
        """Walk a subtree; returns {index_suffix: value}."""
        out: dict[str, Any] = {}
        prefix = base + "."
        async for err_ind, err_stat, _idx, var_binds in walk_cmd(
            self._engine,
            self._auth,
            await self._transport(),
            self._ctx,
            ObjectType(ObjectIdentity(base)),
            lexicographicMode=False,
        ):
            if err_ind:
                raise SnmpError(str(err_ind))
            if err_stat:
                raise SnmpError(err_stat.prettyPrint())
            for oid, val in var_binds:
                s = str(oid)
                if s.startswith(prefix):
                    out[s[len(prefix):]] = val
        return out


def to_int(val: Any) -> int | None:
    try:
        return int(val)
    except (TypeError, ValueError):
        return None


def to_text(val: Any) -> str:
    """Decode an OCTET STRING (incl. UTF-8 bytes) to a clean string."""
    try:
        return val.asOctets().decode("utf-8", "replace").strip("\x00 ").strip()
    except AttributeError:
        return to_str(val).strip()


def to_str(val: Any) -> str:
    try:
        return val.prettyPrint()
    except AttributeError:
        return str(val)
