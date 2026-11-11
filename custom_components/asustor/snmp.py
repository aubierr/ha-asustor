"""Thin async SNMP client built on pysnmp (v7 API)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pysnmp.hlapi.v3arch.asyncio import (
    CommunityData,
    ContextData,
    ObjectIdentity,
    ObjectType,
    SnmpEngine,
    UdpTransportTarget,
    UsmUserData,
    get_cmd,
    usm3DESEDEPrivProtocol,
    usmAesCfb128Protocol,
    usmAesCfb192Protocol,
    usmAesCfb256Protocol,
    usmDESPrivProtocol,
    usmHMAC128SHA224AuthProtocol,
    usmHMAC192SHA256AuthProtocol,
    usmHMAC256SHA384AuthProtocol,
    usmHMAC384SHA512AuthProtocol,
    usmHMACMD5AuthProtocol,
    usmHMACSHAAuthProtocol,
    usmNoAuthProtocol,
    usmNoPrivProtocol,
    walk_cmd,
)

from .const import (
    CONF_AUTH_KEY,
    CONF_AUTH_PROTOCOL,
    CONF_COMMUNITY,
    CONF_PRIV_KEY,
    CONF_PRIV_PROTOCOL,
    CONF_SNMP_VERSION,
    CONF_USERNAME,
    SNMP_V2C,
    SNMP_V3,
)

AUTH_PROTOCOLS = {
    "none": usmNoAuthProtocol,
    "md5": usmHMACMD5AuthProtocol,
    "sha": usmHMACSHAAuthProtocol,
    "sha224": usmHMAC128SHA224AuthProtocol,
    "sha256": usmHMAC192SHA256AuthProtocol,
    "sha384": usmHMAC256SHA384AuthProtocol,
    "sha512": usmHMAC384SHA512AuthProtocol,
}
PRIV_PROTOCOLS = {
    "none": usmNoPrivProtocol,
    "des": usmDESPrivProtocol,
    "3des": usm3DESEDEPrivProtocol,
    "aes": usmAesCfb128Protocol,
    "aes192": usmAesCfb192Protocol,
    "aes256": usmAesCfb256Protocol,
}


def build_auth(data: Mapping[str, Any]) -> CommunityData | UsmUserData:
    """Build the pysnmp credentials from a config entry's data (v2c by default)."""
    if data.get(CONF_SNMP_VERSION, SNMP_V2C) == SNMP_V3:
        auth = data.get(CONF_AUTH_PROTOCOL, "none")
        priv = data.get(CONF_PRIV_PROTOCOL, "none")
        return UsmUserData(
            data[CONF_USERNAME],
            authKey=data.get(CONF_AUTH_KEY) if auth != "none" else None,
            privKey=data.get(CONF_PRIV_KEY) if priv != "none" else None,
            authProtocol=AUTH_PROTOCOLS[auth],
            privProtocol=PRIV_PROTOCOLS[priv],
        )
    return CommunityData(data[CONF_COMMUNITY], mpModel=1)


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


class SnmpAuthError(SnmpError):
    """SNMPv3 credentials were rejected (unknown user, wrong key, wrong security level)."""


# pysnmp error indications are rendered either as the SNMPv3 name or as a sentence.
_AUTH_INDICATIONS = (
    "unknownusername", "unknown usm user",
    "wrongdigest", "wrong snmp pdu digest",
    "unsupportedsecuritylevel", "unsupported snmp security level",
    "decryptionerror", "decryption error", "ciphertext is broken",  # wrong privacy key
)


def _raise_indication(err_ind: Any) -> None:
    text = str(err_ind)
    if any(k in text.lower() for k in _AUTH_INDICATIONS):
        raise SnmpAuthError(text)
    raise SnmpError(text)


class AsustorSnmp:
    """SNMP v2c/v3 client for one NAS."""

    def __init__(
        self, engine: SnmpEngine, host: str, port: int, auth: CommunityData | UsmUserData
    ) -> None:
        self._engine = engine
        self._host = host
        self._port = port
        self._auth = auth
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
            _raise_indication(err_ind)
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
                _raise_indication(err_ind)
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
