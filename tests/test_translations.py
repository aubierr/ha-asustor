"""Every language file must mirror strings.json and keep its {placeholders}; code keys must exist."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from custom_components.asustor.binary_sensor import _specs as binary_specs
from custom_components.asustor.sensor import SYSTEM_SENSORS, _specs as sensor_specs

ROOT = Path(__file__).parent.parent / "custom_components" / "asustor"
FILES = sorted((ROOT / "translations").glob("*.json"))
STRINGS = json.loads((ROOT / "strings.json").read_text(encoding="utf-8"))
PLACEHOLDER = re.compile(r"\{[a-z_]+\}")


def _flatten(node, prefix=""):
    if isinstance(node, dict):
        for k, v in node.items():
            yield from _flatten(v, f"{prefix}.{k}" if prefix else k)
    else:
        yield prefix, node


def test_there_are_many_languages():
    assert len(FILES) >= 23
    assert {"en", "fr", "de", "es", "it", "pt", "pt-BR", "nl", "pl", "ru", "ja", "zh-Hans", "zh-Hant"} <= {f.stem for f in FILES}


def test_english_is_the_source_of_truth():
    assert json.loads((ROOT / "translations" / "en.json").read_text(encoding="utf-8")) == STRINGS


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.stem)
def test_translation_matches_strings(path):
    tr = dict(_flatten(json.loads(path.read_text(encoding="utf-8"))))
    ref = dict(_flatten(STRINGS))
    assert set(tr) == set(ref), f"missing={sorted(set(ref) - set(tr))} extra={sorted(set(tr) - set(ref))}"
    for key, text in tr.items():
        assert text.strip(), f"{key} is empty"
        assert set(PLACEHOLDER.findall(text)) == set(PLACEHOLDER.findall(ref[key])), f"{key}: {text!r}"


def test_every_translation_key_used_by_the_code_exists(hass):
    """Keys referenced by entity descriptions must exist under entity.<platform>."""
    sensor_keys = set(STRINGS["entity"]["sensor"])
    binary_keys = set(STRINGS["entity"]["binary_sensor"])

    # Build a data set that triggers every optional entity (multiple fans/temps, UPS, disk I/O...).
    data = {
        "system": {d.key: 1 for d in SYSTEM_SENSORS},
        "cpus": {"0": {"usage": 1}},
        "fans": {"1": {"speed": 1}, "2": {"speed": 1}},
        "temps": {"1": {"cpu": 1, "system": 1}, "2": {"cpu": 1, "system": 1}},
        "networks": {"1": {"name": "LAN", "ipv4": "a", "ipv6": "b", "mac": "c", "sent": 1, "received": 1}},
        "diskio": {"1": {"name": "sda", "read": 1, "write": 1}},
        "volumes": {"1": {"name": "Volume 1", "usage": 1, "free": 1, "used": 1, "total": 1, "status": "x",
                          "level": "x", "filesystem": "x"}},
        "interfaces": {"eth0": {"name": "eth0", "rx": 1, "tx": 1, "rx_rate": 1, "tx_rate": 1, "link": "up",
                                "speed": 1}},
        "disks": {"1": {"name": "Disk0", "temperature": 1, "health": "x", "status": "x", "size": 1}},
        "ups": {"1": {"name": "U", "status": "OL", "battery_charge": 1, "battery_low": 1, "battery_remaining": 1,
                      "input_voltage": 1, "input_sensitivity": "x", "vendor_id": "x", "product_id": "x"}},
    }
    single = {**data, "fans": {"1": {"speed": 1}}, "temps": {"1": {"cpu": 1, "system": 1}}}
    used_sensor = {spec[3].translation_key for d in (data, single) for spec in sensor_specs(d)}
    used_binary = {spec[3].translation_key for spec in binary_specs(data)}
    assert used_sensor <= sensor_keys, sorted(used_sensor - sensor_keys)
    assert used_binary <= binary_keys, sorted(used_binary - binary_keys)
    assert sensor_keys <= used_sensor, f"unused translations: {sorted(sensor_keys - used_sensor)}"
