"""SNMP helpers: credentials, error mapping, text decoding (pure unit tests)."""

import pytest
from pysnmp.hlapi.v3arch.asyncio import CommunityData, UsmUserData
from pysnmp.proto.rfc1902 import Integer32, OctetString

from custom_components.asustor.snmp import (
    AUTH_PROTOCOLS,
    PRIV_PROTOCOLS,
    SnmpAuthError,
    SnmpError,
    _raise_indication,
    build_auth,
    to_int,
    to_text,
)


def test_build_auth_defaults_to_v2c_for_legacy_entries():
    """Entries created before v3 existed have no snmp_version key."""
    auth = build_auth({"host": "x", "port": 161, "community": "public"})
    assert isinstance(auth, CommunityData)
    assert auth.communityName == "public" or str(auth.communityName) == "public"


def test_build_auth_v3_full():
    auth = build_auth({"snmp_version": "3", "username": "ha", "auth_protocol": "sha256",
                       "auth_key": "authpass12", "priv_protocol": "aes256", "priv_key": "privpass12"})
    assert isinstance(auth, UsmUserData)
    assert auth.userName == "ha"
    assert auth.authentication_protocol == AUTH_PROTOCOLS["sha256"]
    assert auth.privacy_protocol == PRIV_PROTOCOLS["aes256"]


def test_build_auth_v3_auth_only_and_noauth():
    auth_only = build_auth({"snmp_version": "3", "username": "ha", "auth_protocol": "md5",
                            "auth_key": "authpass12", "priv_protocol": "none", "priv_key": ""})
    assert auth_only.privacy_protocol == PRIV_PROTOCOLS["none"]
    no_auth = build_auth({"snmp_version": "3", "username": "ha", "auth_protocol": "none",
                          "priv_protocol": "none"})
    assert no_auth.authentication_protocol == AUTH_PROTOCOLS["none"]


@pytest.mark.parametrize("protocol", sorted(AUTH_PROTOCOLS))
def test_every_auth_protocol_builds(protocol):
    key = "" if protocol == "none" else "authpass12"
    build_auth({"snmp_version": "3", "username": "u", "auth_protocol": protocol, "auth_key": key,
                "priv_protocol": "none"})


@pytest.mark.parametrize("protocol", sorted(PRIV_PROTOCOLS))
def test_every_priv_protocol_builds(protocol):
    key = "" if protocol == "none" else "privpass12"
    build_auth({"snmp_version": "3", "username": "u", "auth_protocol": "sha", "auth_key": "authpass12",
                "priv_protocol": protocol, "priv_key": key})


@pytest.mark.parametrize("text", [
    "Wrong SNMP PDU digest", "wrongDigest", "Unknown USM user", "unknownUserName",
    "Unsupported SNMP security level", "Decryption error",
    "Ciphering services not available or ciphertext is broken",
])
def test_credential_rejections_become_auth_errors(text):
    with pytest.raises(SnmpAuthError):
        _raise_indication(text)


@pytest.mark.parametrize("text", ["No SNMP response received before timeout", "requestTimedOut"])
def test_timeouts_are_plain_errors(text):
    with pytest.raises(SnmpError) as err:
        _raise_indication(text)
    assert not isinstance(err.value, SnmpAuthError)


def test_to_text_decodes_utf8_bytes():
    assert to_text(OctetString("Intel® Celeron™ CPU".encode())) == "Intel® Celeron™ CPU"
    assert to_text(OctetString(b"  padded\x00")) == "padded"


def test_to_int():
    assert to_int(Integer32(5)) == 5
    assert to_int(None) is None
    assert to_int(OctetString(b"abc")) is None
