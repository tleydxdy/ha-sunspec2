"""Tests for the model label helpers.

``device_model_suffix`` feeds ``SunSpecEntity.device_info`` and runs
inside the event loop, so it must stay a pure in-memory lookup: these
tests only cover its resolution order and label cleanup, never file
IO. The file-reading ``sunspec_model_label`` path is exercised via
the config-flow tests.
"""

from custom_components.sunspec2.model_labels import device_model_suffix
from custom_components.sunspec2.model_labels import sunspec_model_label


def test_suffix_uses_spec_label() -> None:
    assert (
        device_model_suffix(
            103, {"name": "inverter_three_phase", "label": "Inverter (Three Phase)"}
        )
        == "Inverter (Three Phase)"
    )


def test_suffix_curated_override_beats_label() -> None:
    # Model 160's official label is "Multiple MPPT Inverter Extension
    # Model", which would bloat every composed entity friendly name.
    assert device_model_suffix(160, {"name": "mppt", "label": "whatever"}) == "MPPT"


def test_suffix_normalises_underscores() -> None:
    # Model 122 is labelled "Measurements_Status" in the spec.
    assert device_model_suffix(122, {"name": "status", "label": "Measurements_Status"}) == (
        "Measurements Status"
    )


def test_suffix_strips_redundant_model_tail() -> None:
    assert device_model_suffix(304, {"name": "inclinometer", "label": "Inclinometer Model"}) == (
        "Inclinometer"
    )


def test_suffix_falls_back_to_group_name() -> None:
    assert device_model_suffix(64201, {"name": "vendor_ext"}) == "vendor ext"


def test_suffix_falls_back_to_model_id() -> None:
    assert device_model_suffix(64201, {}) == "Model 64201"


def test_suffix_without_any_information() -> None:
    # Test stubs may construct entities without a model id; the caller
    # then falls back to the bare Md-based device name.
    assert device_model_suffix(None, {}) is None


def test_sunspec_model_label_resolves_the_foxess_private_names() -> None:
    """The 65xxx vendor-range labels come from FoxESS's own field catalog.

    They are exercised on a Smart WiLAN whose options flow lists every
    block the datalogger serves: "Battery info (65006)" reads like a
    choice, "Model 65006" would not.
    """
    assert sunspec_model_label(65000) == "DER info (65000)"
    assert sunspec_model_label(65006) == "Battery info (65006)"
    assert sunspec_model_label(65026) == "EMS (65026)"


def test_sunspec_model_label_keeps_the_fallback_for_unnamed_fox_blocks() -> None:
    # This firmware serves 65041/65043/65045/65049/65052 with no catalog
    # entry in the front end; the label stays honest instead of guessing.
    assert sunspec_model_label(65041) == "Model 65041"


def test_sunspec_model_label_reads_the_vendors_own_def_file() -> None:
    # 64201/64213 are the relabelled FoxESS layouts inside
    # pysunspec2/models/foxess/.
    assert sunspec_model_label(64201) == "FoxESS AC Measurement (64201)"
    assert sunspec_model_label(64213) == "FoxESS Storage Capacity (64213)"
