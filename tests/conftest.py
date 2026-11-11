"""Shared fixtures: a fake SNMP client replaying a recorded AS5202T (no network, no NAS)."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from pysnmp.proto.rfc1902 import (
    Counter32,
    Counter64,
    Gauge32,
    Integer32,
    ObjectIdentifier,
    OctetString,
    TimeTicks,
)
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.asustor.const import DOMAIN
from custom_components.asustor.snmp import SnmpAuthError, SnmpError

FIXTURE = Path(__file__).parent / "fixtures" / "as5202t.json"
HOST = "192.0.2.18"
V2C_DATA = {"host": HOST, "port": 161, "snmp_version": "2c", "community": "public"}
V3_DATA = {
    "host": HOST, "port": 161, "snmp_version": "3", "username": "ha",
    "auth_protocol": "sha", "auth_key": "authpass12", "priv_protocol": "aes", "priv_key": "privpass12",
}

_TYPES = {"i": Integer32, "u": Gauge32, "t": TimeTicks, "c": Counter64}


def _decode(item: list) -> Any:
    kind, value = item
    if kind == "s":
        return OctetString(value.encode("utf-8"))
    if kind == "o":
        return ObjectIdentifier(value)
    return _TYPES[kind](value)


class FakeSnmp:
    """Drop-in for AsustorSnmp backed by the recorded fixture (mutable between polls)."""

    def __init__(self) -> None:
        data = json.loads(FIXTURE.read_text(encoding="utf-8"))
        self.gets: dict[str, list] = data["gets"]
        self.walks: dict[str, dict[str, list]] = data["walks"]
        self.error: Exception | None = None  # raised by every request when set
        self.auth: Any = None  # credentials the integration built (CommunityData / UsmUserData)
        self.requests = 0
        self.timeouts: list = []  # timeout passed by each client the integration created
        self.v3_accepts: tuple[str, str] | None = None  # (auth, priv) the fake agent accepts, or None = any

    async def get(self, oids: list[str]) -> dict[str, Any]:
        self._check()
        return {o: _decode(self.gets[o]) for o in oids if o in self.gets}

    async def walk(self, base: str) -> dict[str, Any]:
        self._check()
        return {k: _decode(v) for k, v in self.walks.get(base, {}).items()}

    def _check(self) -> None:
        self.requests += 1
        if self.error is not None:
            raise self.error
        if self.v3_accepts is not None and self.auth.__class__.__name__ == "UsmUserData":
            from custom_components.asustor.snmp import AUTH_PROTOCOLS, PRIV_PROTOCOLS

            auth, priv = self.v3_accepts
            if (self.auth.authentication_protocol != AUTH_PROTOCOLS[auth]
                    or self.auth.privacy_protocol != PRIV_PROTOCOLS[priv]):
                raise SnmpAuthError("Wrong SNMP PDU digest")

    # --- test helpers -------------------------------------------------------------------
    def set_get(self, oid: str, kind: str, value: Any) -> None:
        self.gets[oid] = [kind, value]

    def set_walk(self, base: str, suffix: str, kind: str, value: Any) -> None:
        self.walks.setdefault(base, {})[suffix] = [kind, value]

    def add_counter(self, base: str, suffix: str, delta: int) -> None:
        self.walks[base][suffix][1] += delta

    def fail(self, auth: bool = False) -> None:
        self.error = SnmpAuthError("Wrong SNMP PDU digest") if auth else SnmpError("timeout")


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


@pytest.fixture
def fake_snmp():
    """Patch the SNMP client + engine used by the integration and its config flow."""
    fake = FakeSnmp()

    def factory(engine, host, port, auth, **kwargs):
        fake.auth = auth
        fake.timeouts.append(kwargs.get("timeout"))
        return fake

    with (
        patch("custom_components.asustor.AsustorSnmp", factory),
        patch("custom_components.asustor.config_flow.AsustorSnmp", factory),
        patch("custom_components.asustor.create_engine", lambda: MagicMock()),
        patch("custom_components.asustor.config_flow.create_engine", lambda: MagicMock()),
    ):
        yield fake


@pytest.fixture
def make_entry(hass, fake_snmp):
    async def _make(data: dict | None = None, options: dict | None = None) -> MockConfigEntry:
        entry = MockConfigEntry(
            domain=DOMAIN, data=copy.deepcopy(data or V2C_DATA), options=options or {},
            title="NAS-ASUSTOR", unique_id=(data or V2C_DATA)["host"],
        )
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        return entry

    return _make
