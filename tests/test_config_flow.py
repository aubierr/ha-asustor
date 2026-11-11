"""Config, reconfigure, reauth and options flows."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntryState
from homeassistant.data_entry_flow import FlowResultType

from custom_components.asustor.const import DOMAIN

from .conftest import HOST, V2C_DATA, V3_DATA

V3_INPUT = {"username": "ha", "auth_protocol": "sha", "auth_key": "authpass12",
            "priv_protocol": "aes", "priv_key": "privpass12"}


async def _start(hass, version="2c", host=HOST):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "user"
    return await hass.config_entries.flow.async_configure(
        result["flow_id"], {"host": host, "port": 161, "snmp_version": version})


async def test_user_flow_v2c(hass, fake_snmp):
    result = await _start(hass, "2c")
    assert result["step_id"] == "v2c"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"community": "public"})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "NAS-ASUSTOR"  # taken from sysName
    assert result["data"] == V2C_DATA
    await hass.async_block_till_done()


async def test_user_flow_v3(hass, fake_snmp):
    result = await _start(hass, "3")
    assert result["step_id"] == "v3"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], V3_INPUT)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == V3_DATA
    assert fake_snmp.auth.__class__.__name__ == "UsmUserData"
    await hass.async_block_till_done()


async def test_v2c_cannot_connect_then_recovers(hass, fake_snmp):
    fake_snmp.fail()
    result = await _start(hass, "2c")
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"community": "public"})
    assert result["type"] is FlowResultType.FORM and result["errors"] == {"base": "cannot_connect"}
    fake_snmp.error = None
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"community": "public"})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()


async def test_v3_validation_errors(hass, fake_snmp):
    result = await _start(hass, "3")
    flow_id = result["flow_id"]

    short = await hass.config_entries.flow.async_configure(flow_id, {**V3_INPUT, "auth_key": "short"})
    assert short["errors"] == {"base": "key_too_short"}

    no_auth = await hass.config_entries.flow.async_configure(
        flow_id, {**V3_INPUT, "auth_protocol": "none", "auth_key": ""})
    assert no_auth["errors"] == {"base": "priv_requires_auth"}

    fake_snmp.fail(auth=True)
    rejected = await hass.config_entries.flow.async_configure(flow_id, V3_INPUT)
    assert rejected["errors"] == {"base": "invalid_auth"}

    fake_snmp.fail()
    down = await hass.config_entries.flow.async_configure(flow_id, V3_INPUT)
    assert down["errors"] == {"base": "cannot_connect"}


async def test_v3_without_encryption_is_accepted(hass, fake_snmp):
    result = await _start(hass, "3")
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**V3_INPUT, "priv_protocol": "none", "priv_key": ""})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"]["priv_protocol"] == "none" and result["data"]["priv_key"] == ""
    await hass.async_block_till_done()


async def test_duplicate_host_aborts(hass, make_entry):
    await make_entry()
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"host": HOST, "port": 161, "snmp_version": "2c"})
    assert result["type"] is FlowResultType.ABORT and result["reason"] == "already_configured"


async def test_reconfigure_switches_v2c_to_v3(hass, make_entry):
    entry = await make_entry()
    result = await entry.start_reconfigure_flow(hass)
    assert result["step_id"] == "reconfigure"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"host": HOST, "port": 161, "snmp_version": "3"})
    assert result["step_id"] == "v3"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], V3_INPUT)
    assert result["type"] is FlowResultType.ABORT and result["reason"] == "reconfigure_successful"
    await hass.async_block_till_done()
    assert entry.data == V3_DATA and "community" not in entry.data
    assert entry.state is ConfigEntryState.LOADED


async def test_reconfigure_rejects_a_host_used_by_another_entry(hass, make_entry):
    other = await make_entry({**V2C_DATA, "host": "192.0.2.99"})
    await make_entry()
    result = await other.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"host": HOST, "port": 161, "snmp_version": "2c"})
    assert result["type"] is FlowResultType.FORM and result["errors"] == {"base": "already_configured"}


async def test_reauth_updates_v3_credentials(hass, make_entry, fake_snmp):
    fake_snmp.fail(auth=True)
    entry = await make_entry(V3_DATA)
    assert entry.state is ConfigEntryState.SETUP_ERROR
    fake_snmp.error = None
    result = await entry.start_reauth_flow(hass)
    assert result["step_id"] == "v3"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**V3_INPUT, "auth_key": "newpass1234", "priv_key": "newpriv1234"})
    assert result["type"] is FlowResultType.ABORT and result["reason"] == "reauth_successful"
    await hass.async_block_till_done()
    assert entry.data["auth_key"] == "newpass1234"
    assert entry.state is ConfigEntryState.LOADED


async def test_options_flow(hass, make_entry):
    entry = await make_entry()
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "init"
    defaults = {str(k): k.default() for k in result["data_schema"].schema if k.default is not None}
    assert defaults["scan_interval"] == 60
    assert set(defaults["interfaces"]) == {"eth0", "eth1", "bond0"}
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"scan_interval": 120, "interfaces": ["eth0"]})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert entry.options == {"scan_interval": 120, "interfaces": ["eth0"]}
    assert entry.state is ConfigEntryState.LOADED
    assert entry.runtime_data.update_interval.total_seconds() == 120
    assert {i["name"] for i in entry.runtime_data.data["interfaces"].values()} == {"eth0"}
