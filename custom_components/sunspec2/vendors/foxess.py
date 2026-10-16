"""FoxESS: the company's own dataloggers and the blocks they expose.

Covered: the Smart WiLAN / WLan-WW WiFi dataloggers and the datalogger
side of the Fox Hub G2 gateway, bridging an AIO-H1 or one of the H1/AC1
derivatives. FoxESS implements the SunSpec DER measurement models, but
not the way the standard lays them out, and the numbers a user actually
wants (generation counters, battery state) live in private models the
SunSpec registry has no definition for.

Everything below was verified against the capture of a Smart WiLAN at
firmware 1.11.005 bridging an AIO-H1-9.6-US, and against the field map
embedded in the device's own web frontend. What it shows:

* model 701 (DER AC Measurement) is served with FoxESS's own layout:
  the standard's ``DERMode`` point does not exist, so every field after
  ``Alrm`` sits two registers higher than the standard says. The frame
  reads cleanly against the Fox layout: US split phase 244.2 V (L1-L2),
  121.2/121.5 V L1-N/L2-N, 60.02 Hz. Served block: 155 registers.
* model 713 (DER Storage Capacity) is served without the standard's
  ``Pct_SF``, and with a FoxESS ``BackupSOCOffgrid`` point on the tail
  where the standard would read the percent scale factor from. The
  energy fields are plain watt-hours, no scale.
* the private 65xxx blocks between them carry the rest, read from their
  fixed addresses here (the APsystems approach: fixed-address raw
  blocks, decoded outside the scan).

Scale numbers under the private models are fixed per field: the datalogger
reports no scale-factor registers for the curved ones (and its own
frontend applies fixed 0.1 / 0.01 to the fields at these offsets). The
generation counters proved 0.1 kWh against the device's dashboard; the
battery cell voltages are millivolt columns.
"""

from __future__ import annotations

from .foxess_catalog import CATALOG_BLOCKS
from .foxess_catalog import CATALOG_SENSORS
from .profile import ModelRelabel
from .profile import RawBlock
from .profile import RawField
from .profile import RawSensor
from .profile import VendorProfile

#: FoxESS's model 64201 (their AC measurement, 155 registers) and their
#: model 64213 (storage capacity, 9 registers) sit behind the 701 / 713
#: headers the datalogger serves. The definitions are bundled under
#: ``pysunspec2/models/foxess/``, keyed by 64201 / 64213 so they can never
#: collide with a SunSpec model number.
MODEL_RELABELS: tuple[ModelRelabel, ...] = (
    ModelRelabel(701, 153, 64201, 153),
    ModelRelabel(713, 7, 64213, 7),
)

#: The private blocks, at the addresses the datalogger itself reports in
#: ``reg_addr``. Both carry the standard id and length header, so the
#: blocks start with two registers of header and the fields follow.
DER_INFO_ADDRESS = 40958
DER_INFO_COUNT = 158
BATTERY_ADDRESS = 41891
BATTERY_COUNT = 341

RAW_BLOCKS: tuple[RawBlock, ...] = (
    RawBlock(
        key="fox_der_info",
        address=DER_INFO_ADDRESS,
        count=DER_INFO_COUNT,
        model_id=65000,
        fields=(
            RawField("running_state", 2, "uint16"),
            RawField("alarm_code", 3, "uint16"),
            RawField("daily_power_gen", 4, "uint32", scale=0.1),
            RawField("monthly_power_gen", 6, "uint32", scale=0.1),
            RawField("annual_power_gen", 8, "uint32", scale=0.1),
            RawField("total_on_grid_time", 10, "uint32"),
            RawField("daily_on_grid_time", 12, "uint16"),
            RawField("bus_voltage", 14, "uint16", scale=0.1),
            RawField("array_insulation", 15, "uint16", scale=0.1),
            RawField("on_grid_voltage_a", 119, "uint16", scale=0.1),
            RawField("on_grid_voltage_b", 120, "uint16", scale=0.1),
            RawField("on_grid_voltage_c", 121, "uint16", scale=0.1),
            RawField("off_grid_voltage_a", 122, "uint16", scale=0.1),
            RawField("off_grid_voltage_b", 123, "uint16", scale=0.1),
            RawField("off_grid_voltage_c", 124, "uint16", scale=0.1),
            RawField("module_temperature_1", 125, "int16", scale=0.1),
            RawField("module_temperature_2", 126, "int16", scale=0.1),
            RawField("module_temperature_3", 127, "int16", scale=0.1),
            RawField("grid_ne_voltage", 128, "int16", scale=0.1),
            RawField("load_ne_voltage", 129, "int16", scale=0.1),
            RawField("positive_bus_voltage", 130, "uint16", scale=0.1),
            RawField("negative_bus_voltage", 131, "uint16", scale=0.1),
        ),
    ),
    RawBlock(
        key="fox_battery",
        address=BATTERY_ADDRESS,
        count=BATTERY_COUNT,
        model_id=65006,
        fields=(
            RawField("charge_cutoff_voltage", 2, "uint16", scale=0.1),
            RawField("discharge_cutoff_voltage", 3, "uint16", scale=0.1),
            RawField("max_charge_current", 4, "uint16", scale=0.1),
            RawField("max_discharge_current", 5, "uint16", scale=0.1),
            RawField("rack_voltage", 6, "uint16", scale=0.1),
            RawField("rack_current", 7, "int16", scale=0.1),
            RawField("battery_soc", 8, "uint16"),
            # RemainEnergy at 9 is not curated: it duplicates the 713
            # WHAvail and reads 0xFF sentinel fields when the battery
            # sleeps. The cell voltages at 10-13 carry the same 0.1 V
            # scale the frontend applies.
            RawField("max_temperature", 10, "int16", scale=0.1),
            RawField("min_temperature", 11, "int16", scale=0.1),
            RawField("max_cell_voltage", 12, "uint16", scale=0.1),
            RawField("min_cell_voltage", 13, "uint16", scale=0.1),
            RawField("ambient_temperature", 14, "int16", scale=0.1),
            RawField("cycle_count", 19, "uint16"),
            RawField("battery_max_cell_voltage", 20, "uint16", scale=0.001),
            RawField("battery_min_cell_voltage", 21, "uint16", scale=0.001),
            RawField("energy_throughput", 29, "uint32"),
        ),
    ),
)

RAW_SENSORS: tuple[RawSensor, ...] = (
    RawSensor(
        block="fox_der_info",
        field="running_state",
        key="fox_running_state",
        diagnostic=True,
        icon="mdi:engine",
    ),
    RawSensor(
        block="fox_der_info",
        field="alarm_code",
        key="fox_alarm_code",
        diagnostic=True,
        icon="mdi:alert-circle-outline",
    ),
    RawSensor(
        block="fox_der_info",
        field="daily_power_gen",
        key="fox_daily_generation",
        unit="kWh",
        device_class="energy",
        state_class="total_increasing",
        icon="mdi:solar-power",
    ),
    RawSensor(
        block="fox_der_info",
        field="monthly_power_gen",
        key="fox_monthly_generation",
        unit="kWh",
        device_class="energy",
        state_class="total_increasing",
        icon="mdi:calendar-month",
    ),
    RawSensor(
        block="fox_der_info",
        field="annual_power_gen",
        key="fox_annual_generation",
        unit="kWh",
        device_class="energy",
        state_class="total_increasing",
        icon="mdi:calendar-month",
    ),
    RawSensor(
        block="fox_der_info",
        field="total_on_grid_time",
        key="fox_total_on_grid_time",
        unit="h",
        state_class="total_increasing",
        icon="mdi:timelapse",
    ),
    RawSensor(
        block="fox_der_info",
        field="on_grid_voltage_a",
        key="fox_on_grid_voltage_ab",
        unit="V",
        device_class="voltage",
        state_class="measurement",
        icon="mdi:sine-wave",
    ),
    RawSensor(
        block="fox_der_info",
        field="on_grid_voltage_b",
        key="fox_on_grid_voltage_bc",
        unit="V",
        device_class="voltage",
        state_class="measurement",
        icon="mdi:sine-wave",
    ),
    RawSensor(
        block="fox_der_info",
        field="off_grid_voltage_a",
        key="fox_off_grid_voltage_ab",
        unit="V",
        device_class="voltage",
        state_class="measurement",
        icon="mdi:sine-wave",
        enabled_by_default=False,
    ),
    RawSensor(
        block="fox_der_info",
        field="off_grid_voltage_b",
        key="fox_off_grid_voltage_bc",
        unit="V",
        device_class="voltage",
        state_class="measurement",
        icon="mdi:sine-wave",
        enabled_by_default=False,
    ),
    RawSensor(
        block="fox_der_info",
        field="module_temperature_1",
        key="fox_module_temperature_1",
        unit="°C",
        device_class="temperature",
        state_class="measurement",
        diagnostic=True,
        icon="mdi:thermometer",
    ),
    RawSensor(
        block="fox_der_info",
        field="module_temperature_2",
        key="fox_module_temperature_2",
        unit="°C",
        device_class="temperature",
        state_class="measurement",
        diagnostic=True,
        icon="mdi:thermometer",
    ),
    RawSensor(
        block="fox_der_info",
        field="bus_voltage",
        key="fox_bus_voltage",
        unit="V",
        device_class="voltage",
        state_class="measurement",
        diagnostic=True,
        icon="mdi:current-dc",
    ),
    RawSensor(
        block="fox_der_info",
        field="array_insulation",
        key="fox_array_insulation",
        unit="kΩ",
        state_class="measurement",
        diagnostic=True,
        icon="mdi:gauge",
    ),
    RawSensor(
        block="fox_battery",
        field="battery_soc",
        key="fox_battery_soc",
        unit="%",
        state_class="measurement",
        icon="mdi:battery-50",
    ),
    RawSensor(
        block="fox_battery",
        field="rack_voltage",
        key="fox_battery_voltage",
        unit="V",
        device_class="voltage",
        state_class="measurement",
        icon="mdi:battery",
    ),
    RawSensor(
        block="fox_battery",
        field="rack_current",
        key="fox_battery_current",
        unit="A",
        device_class="current",
        state_class="measurement",
        icon="mdi:battery-arrow-up-down",
    ),
    RawSensor(
        block="fox_battery",
        field="max_cell_voltage",
        key="fox_battery_cell_voltage_max",
        unit="V",
        device_class="voltage",
        state_class="measurement",
        diagnostic=True,
        icon="mdi:battery-positive",
    ),
    RawSensor(
        block="fox_battery",
        field="min_cell_voltage",
        key="fox_battery_cell_voltage_min",
        unit="V",
        device_class="voltage",
        state_class="measurement",
        diagnostic=True,
        icon="mdi:battery-negative",
    ),
    RawSensor(
        block="fox_battery",
        field="max_temperature",
        key="fox_battery_temperature_max",
        unit="°C",
        device_class="temperature",
        state_class="measurement",
        diagnostic=True,
        icon="mdi:thermometer",
    ),
    RawSensor(
        block="fox_battery",
        field="min_temperature",
        key="fox_battery_temperature_min",
        unit="°C",
        device_class="temperature",
        state_class="measurement",
        diagnostic=True,
        icon="mdi:thermometer",
    ),
    RawSensor(
        block="fox_battery",
        field="cycle_count",
        key="fox_battery_cycle_count",
        state_class="total_increasing",
        diagnostic=True,
        icon="mdi:battery-clock",
    ),
    RawSensor(
        block="fox_battery",
        field="energy_throughput",
        key="fox_battery_energy_throughput",
        unit="Wh",
        device_class="energy_storage",
        state_class="total_increasing",
        icon="mdi:battery-sync",
    ),
)

#: Every control the write platforms could see on the relabelled models is
#: unverified on this device path: writes are refused at the transport
#: until they are exercised on hardware, so no Number / Select entity says
#: otherwise. The write platforms only build controls for models 123, 124
#: and 704, none of which this datalogger serves, so nothing else is
#: gated (see number.build_specs).


#: The FoxESS private model names, straight from the datalogger's own
#: frontend field catalog (the same map the raw block fields are sourced
#: from). SunSpec reserves the 65000 range for vendor models, so these
#: ids mean FoxESS blocks everywhere they appear. The blocks the
#: frontend does not name (this firmware serves 65041, 65043, 65045,
#: 65049, 65052 as parameter and log data with no catalog entry) keep
#: the generic "Model <id>" fallback rather than a guess.
FOXESS_MODEL_LABELS: dict[int, str] = {
    65000: "DER info",
    65001: "DER Debug info",
    65002: "Param",
    65003: "DbgParam",
    65004: "DER Storage Capacity info",
    65005: "DER property info",
    65006: "Battery info",
    65007: "Param2",
    65008: "DER info2",
    65009: "R&D(BatteryInfo)",
    65010: "EMS-TOU",
    65011: "Smart Circuit",
    65012: "Time & Country",
    65013: "Advanced(Grid Imbalance Protection)",
    65014: "Advanced(Protect Recovery)",
    65015: "Advanced(Ileak & DCI)",
    65016: "Advanced(Islanding Parameters)",
    65017: "Advanced(SVG & PID)",
    65018: "Advanced(String &PE Monitoring)",
    65019: "R&D(FaultMasking)",
    65020: "Advanced(GlobalMPPTScaning)",
    65021: "Advanced(Others)",
    65022: "Advanced(Active Power)",
    65023: "ExportLimit",
    65024: "Advanced(OPU &UPU)",
    65025: "Advanced(Reactive PFP)",
    65026: "EMS",
    65027: "R&D(Others)",
    65028: "Advanced(MasterSlaveParam)",
    65029: "R&D(DebugParam)",
    65030: "R&D(MeterDebug)",
    65031: "R&D(HubInfo)",
    65032: "ParallelParam",
    65033: "AC-Couple",
    65034: "EMS-Manual",
    65035: "Advanced(SlaveAddress)",
    65036: "EMS-ExportCompensation",
    65037: "OffGrid-Time",
    65038: "ODM-Info",
    65039: "Generator",
}

FOXESS = VendorProfile(
    slug="foxess",
    manufacturer_prefixes=("BFOX", "FoxESS", "FOX"),
    model_relabels=MODEL_RELABELS,
    raw_blocks=RAW_BLOCKS + CATALOG_BLOCKS,
    raw_sensors=RAW_SENSORS + CATALOG_SENSORS,
    default_model_ids=frozenset({64201, 64213, 65000, 65004, 65006, 65012}),
)

# The FoxESS models whose private blocks the entry polls behind the
# multi-select. The migration in ``__init__.py`` folds these into the
# saved model ticks, so installs that existed before raw blocks became
# tick-gated keep their FoxESS data on the update.
FOX_MIRROR_MODEL_IDS: frozenset[int] = frozenset({65000, 65004, 65006, 65012})
