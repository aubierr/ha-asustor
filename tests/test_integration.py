"""Setup, coordinator data, devices, entities, binary sensors, update entity, pruning."""

from __future__ import annotations

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.helpers import device_registry as dr, entity_registry as er

from custom_components.asustor.binary_sensor import (
    disk_problem,
    link_up,
    ups_low_battery,
    ups_on_battery,
    volume_problem,
)
from custom_components.asustor.const import DOMAIN
from custom_components.asustor.device import prune_stale_devices
from custom_components.asustor.update import update_available

from .conftest import V2C_DATA, V3_DATA

DISK_HEALTH = "1.3.6.1.4.1.44738.4.1.1.6"
VOLUME_STATUS = "1.3.6.1.4.1.44738.5.1.1.4"
IF_HC_IN = "1.3.6.1.2.1.31.1.1.1.6"
UPS = "1.3.6.1.4.1.44738.6.1.1"


def _entities(hass, entry):
    return {e.unique_id.split("_", 1)[1]: e for e in er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)}


def _state(hass, ents, uid):
    return hass.states.get(ents[uid].entity_id)


async def test_setup_and_unload(hass, make_entry):
    entry = await make_entry()
    assert entry.state is ConfigEntryState.LOADED
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert entry.state is ConfigEntryState.NOT_LOADED


async def test_coordinator_data_matches_the_recorded_nas(hass, make_entry):
    data = (await make_entry()).runtime_data.data
    assert data["system"]["cpu_usage"] == 6  # 94 % idle in the recording
    assert data["system"]["memory_total"] == pytest.approx(7.54, abs=0.01)
    assert data["temps"]["1"] == {"cpu": 45, "system": 38}
    assert data["fans"]["1"]["speed"] == 1123
    assert set(data["cpus"]) == {"0", "1"}
    assert data["volumes"]["1"]["level"] == "raid1" and data["volumes"]["1"]["usage"] == pytest.approx(50.4, abs=0.1)
    assert data["disks"]["1"]["temperature"] == 38 and data["disks"]["1"]["health"] == "Healthy"
    assert data["disks"]["2"]["model"] == "ST4000VN006-3CW104"
    assert {i["name"] for i in data["interfaces"].values()} == {"eth0", "eth1", "bond0"}
    assert data["ups"] == {}  # the recorded NAS has no UPS; the phantom row must be ignored
    assert data["networks"]["1"]["ipv4"] == "192.0.2.18"


async def test_devices_are_grouped_and_linked_to_the_nas(hass, make_entry):
    entry = await make_entry()
    reg = dr.async_get(hass)
    devices = {d.name: d for d in dr.async_entries_for_config_entry(reg, entry.entry_id)}
    assert set(devices) == {"NAS-ASUSTOR", "NAS-ASUSTOR Disk0", "NAS-ASUSTOR Disk1", "NAS-ASUSTOR Volume 1",
                            "NAS-ASUSTOR eth0", "NAS-ASUSTOR eth1", "NAS-ASUSTOR bond0"}
    nas = devices["NAS-ASUSTOR"]
    assert nas.model == "AS5202T" and nas.manufacturer == "ASUSTOR" and nas.sw_version == "5.1.4.RM62"
    for name, dev in devices.items():
        if name != "NAS-ASUSTOR":
            assert dev.via_device_id == nas.id, name
    assert devices["NAS-ASUSTOR Disk0"].model == "ST4000VN006-3CW104"


async def test_entity_names_use_placeholders(hass, make_entry):
    ents = _entities(hass, await make_entry())
    names = {uid: _state(hass, ents, uid).attributes["friendly_name"] for uid in
             ("cpu0", "net1_ipv4", "vol1_usage", "disk1_temp", "ifeth0_rx_rate", "fan1", "temp1_cpu")}
    assert names == {
        "cpu0": "NAS-ASUSTOR CPU core 0 usage",
        "net1_ipv4": "NAS-ASUSTOR LAN 1+2 IPv4 address",
        "vol1_usage": "NAS-ASUSTOR Volume 1 Usage",
        "disk1_temp": "NAS-ASUSTOR Disk0 Temperature",
        "ifeth0_rx_rate": "NAS-ASUSTOR eth0 Receive rate",
        "fan1": "NAS-ASUSTOR Fan speed",
        "temp1_cpu": "NAS-ASUSTOR CPU temperature",
    }


async def test_healthy_nas_has_no_problems_and_update_is_off(hass, make_entry):
    entry = await make_entry()
    ents = _entities(hass, entry)
    for uid in ("disk1_problem", "disk2_problem", "vol1_problem"):
        assert _state(hass, ents, uid).state == "off", uid
    assert _state(hass, ents, "ifeth0_connected").state == "on"
    upd = hass.states.get(next(e.entity_id for e in ents.values() if e.domain == "update"))
    assert upd.state == "off" and upd.attributes["installed_version"] == "5.1.4.RM62"


async def test_faults_turn_binary_sensors_on(hass, make_entry, fake_snmp):
    entry = await make_entry()
    ents = _entities(hass, entry)
    fake_snmp.set_walk(DISK_HEALTH, "2", "s", "Bad")
    fake_snmp.set_walk(VOLUME_STATUS, "1", "s", "degraded")
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert _state(hass, ents, "disk1_problem").state == "off"
    assert _state(hass, ents, "disk2_problem").state == "on"
    assert _state(hass, ents, "vol1_problem").state == "on"
    assert _state(hass, ents, "vol1_problem").attributes["status"] == "degraded"


async def test_update_entity_reports_available(hass, make_entry, fake_snmp):
    entry = await make_entry()
    fake_snmp.set_get("1.3.6.1.4.1.44738.1.7.0", "s", "Available")
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    eid = next(e.entity_id for e in _entities(hass, entry).values() if e.domain == "update")
    assert hass.states.get(eid).state == "on"


async def test_throughput_is_computed_from_counter_deltas(hass, make_entry, fake_snmp):
    entry = await make_entry()
    ents = _entities(hass, entry)
    assert _state(hass, ents, "ifeth0_rx_rate").state == "unknown"  # needs two polls
    assert entry.runtime_data.data["interfaces"]["eth0"]["rx_rate"] is None
    # 2 index of eth0 in the recording is "2": add exactly 125 MB -> 1000 Mbit over the interval
    fake_snmp.add_counter(IF_HC_IN, "2", 125_000_000)
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    rate = float(_state(hass, ents, "ifeth0_rx_rate").state)
    assert rate > 0
    assert _state(hass, ents, "ifeth0_rx_rate").attributes["unit_of_measurement"] == "Mbit/s"


async def test_counter_reset_skips_one_sample(hass, make_entry, fake_snmp):
    entry = await make_entry()
    await entry.runtime_data.async_refresh()
    fake_snmp.set_walk(IF_HC_IN, "2", "c", 1)  # counter went backwards (NAS rebooted / wrapped)
    await entry.runtime_data.async_refresh()
    assert entry.runtime_data.data["interfaces"]["eth0"]["rx_rate"] is None


async def test_options_limit_interfaces_and_interval(hass, make_entry):
    entry = await make_entry(options={"scan_interval": 30, "interfaces": ["eth1"]})
    assert entry.runtime_data.update_interval.total_seconds() == 30
    assert {i["name"] for i in entry.runtime_data.data["interfaces"].values()} == {"eth1"}


async def test_ups_creates_a_device_and_binary_sensors(hass, make_entry, fake_snmp):
    for col, kind, value in ((2, "s", "APC"), (3, "s", "Back-UPS"), (7, "s", "OB LB"), (8, "i", 1200),
                             (9, "i", 35), (10, "i", 20), (11, "i", 230)):
        fake_snmp.set_walk(f"{UPS}.{col}", "1", kind, value)
    entry = await make_entry()
    devices = {d.name for d in dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)}
    assert "NAS-ASUSTOR APC Back-UPS" in devices
    ents = _entities(hass, entry)
    assert _state(hass, ents, "ups1_on_battery").state == "on"
    assert _state(hass, ents, "ups1_low_battery").state == "on"
    assert _state(hass, ents, "ups1_battery_charge").state == "35"
    assert _state(hass, ents, "ups1_input_voltage").state == "230"


async def test_unreachable_nas_fails_setup_and_retries(hass, make_entry, fake_snmp):
    fake_snmp.fail()
    entry = await make_entry()
    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_rejected_v3_credentials_start_reauth(hass, make_entry, fake_snmp):
    fake_snmp.fail(auth=True)
    entry = await make_entry(V3_DATA)
    assert entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert any(f["context"]["source"] == "reauth" for f in flows)
    assert fake_snmp.auth.__class__.__name__ == "UsmUserData"


async def test_legacy_v2c_entry_without_version_key_still_loads(hass, make_entry, fake_snmp):
    legacy = {k: v for k, v in V2C_DATA.items() if k != "snmp_version"}
    entry = await make_entry(legacy)
    assert entry.state is ConfigEntryState.LOADED
    assert fake_snmp.auth.__class__.__name__ == "CommunityData"


async def test_stale_devices_are_removed_after_three_polls(hass, make_entry):
    entry = await make_entry()
    reg = dr.async_get(hass)
    stale = reg.async_get_or_create(config_entry_id=entry.entry_id, name="old UPS",
                                    identifiers={(DOMAIN, f"{entry.entry_id}_ups_1")})
    data, missing = dict(entry.runtime_data.data), {}
    for _ in range(2):
        prune_stale_devices(hass, entry, data, missing)
        assert reg.async_get(stale.id) is not None
    prune_stale_devices(hass, entry, data, missing)
    assert reg.async_get(stale.id) is None


async def test_empty_disk_or_volume_table_never_prunes(hass, make_entry):
    entry = await make_entry()
    reg = dr.async_get(hass)
    before = {d.id for d in dr.async_entries_for_config_entry(reg, entry.entry_id)}
    data, missing = {**entry.runtime_data.data, "disks": {}, "volumes": {}}, {}
    for _ in range(6):
        prune_stale_devices(hass, entry, data, missing)
    assert {d.id for d in dr.async_entries_for_config_entry(reg, entry.entry_id)} == before


async def test_present_devices_cannot_be_deleted_from_the_ui(hass, make_entry):
    from custom_components.asustor import async_remove_config_entry_device

    entry = await make_entry()
    reg = dr.async_get(hass)
    devices = {d.name: d for d in dr.async_entries_for_config_entry(reg, entry.entry_id)}
    assert not await async_remove_config_entry_device(hass, entry, devices["NAS-ASUSTOR"])
    assert not await async_remove_config_entry_device(hass, entry, devices["NAS-ASUSTOR Disk0"])
    gone = reg.async_get_or_create(config_entry_id=entry.entry_id, name="gone",
                                   identifiers={(DOMAIN, f"{entry.entry_id}_disk_9")})
    assert await async_remove_config_entry_device(hass, entry, gone)


# --- pure functions --------------------------------------------------------------------------
@pytest.mark.parametrize(("row", "expected"), [
    ({"health": "Healthy", "status": "Good"}, False), ({"health": "Normal", "status": "Idle"}, False),
    ({"health": "Risky"}, True), ({"health": "Dangerous"}, True), ({"health": "Bad"}, True),
    ({"health": "Healthy", "status": "Abnormal"}, True), ({}, None),
])
def test_disk_problem(row, expected):
    assert disk_problem(row) is expected


@pytest.mark.parametrize(("status", "expected"), [
    ("healthy", False), ("clean", False), ("active", False), ("resyncing", False), ("reshaping", False),
    ("degraded", True), ("failed", True), ("Inactive", True), (None, None),
])
def test_volume_problem(status, expected):
    assert volume_problem({"status": status}) is expected


def test_link_and_ups_flags():
    assert link_up({"link": "up"}) is True and link_up({"link": "down"}) is False and link_up({}) is None
    assert ups_on_battery({"status": "OB"}) is True and ups_on_battery({"status": "OL"}) is False
    assert ups_low_battery({"status": "OB LB"}) is True and ups_low_battery({"status": "OL"}) is False
    assert ups_on_battery({}) is None and ups_low_battery({}) is None


@pytest.mark.parametrize(("text", "expected"), [
    ("The ADM is latest version", False), ("Unavailable", False), ("Available", True),
    ("A new version is available", True), ("", None), (None, None),
])
def test_update_available(text, expected):
    assert update_available(text) is expected
