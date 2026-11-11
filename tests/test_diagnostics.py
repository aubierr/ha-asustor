"""Diagnostics must be useful for support but never leak private values."""

from custom_components.asustor.diagnostics import async_get_config_entry_diagnostics

from .conftest import HOST


async def test_diagnostics_are_redacted(hass, make_entry):
    entry = await make_entry()
    diag = await async_get_config_entry_diagnostics(hass, entry)

    text = str(diag)
    for secret in (HOST, "public", "ASUSTOR-TEST-SERIAL", "user@example.com", "02:00:00:00:00:01", "(GMT+00:00) UTC"):
        assert secret not in text, f"leaked: {secret}"

    assert diag["device"] == {"model": "AS5202T", "adm_version": "5.1.4.RM62", "bios_version": "1.25"}
    assert diag["entry"]["data"]["community"] == "**REDACTED**"
    assert diag["entry"]["data"]["host"] == "**REDACTED**"
    assert diag["last_update_success"] is True and diag["update_interval_s"] == 60

    raw = diag["asustor_mib_walk"]
    assert raw["1.3.6.1.4.1.44738.1.1.0"] == "**REDACTED**"  # serial
    assert raw["1.3.6.1.4.1.44738.1.8.0"] == "**REDACTED**"  # ASUSTOR ID (e-mail)
    assert raw["1.3.6.1.4.1.44738.3.1.1.4.1"] == "**REDACTED**"  # IPv4
    assert raw["1.3.6.1.4.1.44738.2.1.0"] == "AS5202T"  # model is kept: that is the point of the dump
    assert len(raw) > 30


async def test_diagnostics_hide_v3_secrets(hass, make_entry):
    from .conftest import V3_DATA

    entry = await make_entry(V3_DATA)
    text = str(await async_get_config_entry_diagnostics(hass, entry))
    for secret in ("authpass12", "privpass12", "'ha'"):
        assert secret not in text, secret
