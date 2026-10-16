"""The FoxESS vendor profile end to end over the captured register image.

Everything runs against the fixture in ``tests/test_data/fox_wilan.json``:
one full capture of a Smart WiLAN at firmware 1.11.005 bridging an
AIO-H1-9.6-US over its HTTP relay, reads only. The vendor knowledge is
exercised the way production reaches it: the api client, the unit device
over the fake connection, the scan, the vendor relabel pass, and the raw
block decoder.
"""

import json
import struct
from pathlib import Path

import pytest

from custom_components.sunspec2.api import SunSpecApiClient
from custom_components.sunspec2.raw_blocks import decode_block
from custom_components.sunspec2.vendors import default_models_for_profile
from custom_components.sunspec2.vendors import profile_for
from custom_components.sunspec2.vendors.foxess import FOXESS
from custom_components.sunspec2.vendors.foxess_catalog import CATALOG_BLOCKS
from custom_components.sunspec2.vendors.foxess_catalog import CATALOG_SENSORS

from .fake_unit import FakeConnection
from .fake_unit import FakeUnit

_FIXTURE = Path(__file__).parent / "test_data" / "fox_wilan.json"

# Values the tests assert, straight out of the captured image. They are
# the device's own readings: a late-morning capture with the battery at
# 66 percent and the array producing 200 W under cloud.
EXPECTED = {
    "hz": 59.98,
    "watts": 200.0,
    "power_factor": 1.0,
    "vl1l2": 244.7,
    "vl1": 121.5,
    "tot_wh_inj": 14900.0,
    "soc": 66.0,
    "soh": 100.0,
    "wh_rated": 7950.0,
    "wh_available": 4452.0,
    "backup_soc": 30.0,
}

# The battery the profile's raw block reads and what decode_block() must
# produce over the fixture image, scales included.
BATTERY_FIELDS = {
    "battery_soc": 66.0,
    "rack_voltage": 119.0,
    "rack_current": -1.9,
    "max_temperature": 14.6,
    "min_temperature": 13.4,
    "max_cell_voltage": 3.3,
    "min_cell_voltage": 3.3,
    "battery_max_cell_voltage": 3.308,
    "battery_min_cell_voltage": 3.306,
    "energy_throughput": 24941.0,
}


def _fox_image() -> dict[int, int]:
    data = json.loads(_FIXTURE.read_text())
    return {int(address): word for address, word in data["image"].items()}


def _api(hass, connection, **kwargs):
    """An api client whose connection object is the fake, the way test_unit_device builds it."""
    api = SunSpecApiClient(host="fox.test", port=80, unit_id=1, hass=hass, **kwargs)
    api._connection = connection
    return api


def _fox_api(hass):
    connection = FakeConnection({1: FakeUnit(holding=_fox_image(), unit_id=1)})
    return _api(hass, connection)


def _raw_block_bytes(address: int, count: int, words_at: dict[int, int]) -> bytes:
    return struct.pack(f">{count}H", *[words_at[address + i] for i in range(count)])


async def test_the_vendor_profile_is_picked_from_the_manufacturer():
    assert profile_for("BFOX") is FOXESS
    assert profile_for("FoxESS") is FOXESS
    assert profile_for("FOX") is FOXESS
    assert profile_for("Fronius") is not FOXESS
    assert profile_for(None) is None


def test_default_models_for_profile_pre_ticks_what_the_device_carries():
    profile = FOXESS
    assert default_models_for_profile(profile, {1, 64201, 64213}) == {64201, 64213}
    # The pre-tick list stays behind what the device reports.
    assert default_models_for_profile(profile, {1}) == set()
    # A generic device with no profile pre-ticks nothing extra.
    assert default_models_for_profile(None, {1, 64201}) == set()


async def test_the_fox_layouts_relabel_the_blocks_they_describe(hass):
    api = _fox_api(hass)
    models = await api.async_get_models()

    # The relabelled ids carry the FoxESS layouts.
    assert 64201 in models
    assert 64213 in models
    # The headers they were relabelled from are gone; the private blocks
    # the SunSpec registry has no definition for keep their header ids.
    assert 701 not in models
    assert 713 not in models
    assert 65000 in models


async def test_the_ac_measurement_reads_the_fox_layout_up_to_the_device_scales(hass):
    api = _fox_api(hass)
    ac = await api.async_get_data(64201)

    assert ac.getValue("Hz") == pytest.approx(EXPECTED["hz"])
    assert ac.getValue("W") == pytest.approx(EXPECTED["watts"])
    assert ac.getValue("PF") == pytest.approx(EXPECTED["power_factor"])
    assert ac.getValue("VL1L2") == pytest.approx(EXPECTED["vl1l2"])
    assert ac.getValue("VL1") == pytest.approx(EXPECTED["vl1"])
    assert ac.getValue("TotWhInj") == pytest.approx(EXPECTED["tot_wh_inj"])


async def test_the_storage_block_reads_the_fox_layout(hass):
    api = _fox_api(hass)
    storage = await api.async_get_data(64213)

    assert storage.getValue("StateOfCharge") == pytest.approx(EXPECTED["soc"])
    assert storage.getValue("StateOfHealth") == pytest.approx(EXPECTED["soh"])
    # The watt-hour fields carry FoxESS's convention: raw, no scale.
    assert storage.getValue("WHRtg") == pytest.approx(EXPECTED["wh_rated"])
    assert storage.getValue("WHAvail") == pytest.approx(EXPECTED["wh_available"])
    assert storage.getValue("BackupSOCOffgrid") == pytest.approx(EXPECTED["backup_soc"])


async def test_the_private_blocks_decode_through_the_raw_blocks():
    fixture = json.loads(_FIXTURE.read_text())
    words = {int(address): word for address, word in fixture["image"].items()}

    battery_block = FOXESS.raw_block("fox_battery")
    assert battery_block is not None
    decoded = decode_block(
        battery_block,
        _raw_block_bytes(battery_block.address, battery_block.count, words),
    )
    for field, expected in BATTERY_FIELDS.items():
        assert decoded[field] == pytest.approx(expected), field

    info_block = FOXESS.raw_block("fox_der_info")
    assert info_block is not None
    info = decode_block(
        info_block,
        _raw_block_bytes(info_block.address, info_block.count, words),
    )
    assert info["daily_power_gen"] == pytest.approx(8.2)
    assert info["monthly_power_gen"] == pytest.approx(14.9)
    assert info["on_grid_voltage_a"] == pytest.approx(121.3)
    assert info["positive_bus_voltage"] == pytest.approx(193.6)


def test_every_catalog_block_decodes_over_the_captured_image():
    """The generated blocks must decode cleanly against the capture.

    The catalog mirror is generated from the device's own field catalog;
    an offset or size mistake shows up here as a DeviceError (short
    answer) or a ValueError (unknown kind), not as wrong numbers.
    """
    fixture = json.loads(_FIXTURE.read_text())
    words = {int(address): word for address, word in fixture["image"].items()}
    for block in CATALOG_BLOCKS:
        data = _raw_block_bytes(block.address, block.count, words)
        decoded = decode_block(block, data)
        assert decoded, block.key


def test_every_catalog_sensor_names_a_real_field_and_is_translated():
    """Each generated sensor points into its block, uniquely, with a name."""
    from pathlib import Path as path

    en = json.loads(
        path(__file__)
        .parents[1]
        .joinpath("custom_components/sunspec2/translations/en.json")
        .read_text()
    )
    names = en["entity"]["sensor"]
    blocks = {block.key: block for block in CATALOG_BLOCKS}
    seen: set[str] = set()
    for spec in CATALOG_SENSORS:
        assert spec.key not in seen, spec.key
        seen.add(spec.key)
        block = blocks[spec.block]
        assert spec.field in {f.name for f in block.fields}, spec.key
        assert spec.key in names, spec.key
        assert names[spec.key].get("name"), spec.key
