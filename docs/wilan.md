# FoxESS Smart WiLAN (HTTP relay)

How to set up a FoxESS inverter whose WiFi datalogger is a **Smart
WiLAN** (the standalone "WiLAN-xxxxxx" dongle, the **WLan-WW** logger
built into 2026 hardware, or the datalogger side of a **Fox Hub G2**).

These devices do not speak Modbus TCP or Modbus RTU over the network on
any port. They serve a small HTTP interface on port 80 instead, behind
the same web frontend you see when you join their WiFi; the integration
relays Modbus frames through it.

## Setup

1. Put the datalogger on your LAN (the "sta" network settings in its own
   web interface) and note its IP address.
2. In Home Assistant: Settings, Devices and Services, Add Integration,
   *SunSpec 2*, pick **FoxESS Smart WiLAN datalogger - HTTP**.
3. Enter the datalogger's IP and the Modbus unit ID of the inverter
   behind it. Most devices answer on unit ID 1.

There is deliberately no port field: port 80 is the only thing this
firmware serves.

## What you get

The vendor profile (picked from the `BFOX`/`FOX` manufacturer string in
common model 1) pre-ticks the FoxESS model ids and reads the private
blocks, so a configured device offers:

* the inverter's AC data from **model 64201** (FoxESS's own layout of
  the DER AC measurement block: active/apparent/reactive power, power
  factor, frequency, phase voltages and currents, heat sink
  temperature, alarm bitfield, lifetime energy injected),
* the storage data from **model 64213** (state of charge and health,
  energy rating and available energy, off-grid backup SoC), and
* the generation counters and battery detail over the private models,
  read from fixed addresses: daily / monthly / annual generation,
  on-grid and off-grid phase voltages, module temperatures, DC bus
  voltage, battery SoC, rack voltage and current, cell voltages,
  battery temperatures, cycle count and energy throughput.

The private blocks live at two fixed addresses (the DER info block at
40958, the battery block at 41891 in this capture). The field meanings
were recovered from the datalogger's own frontend and checked against
live readings, but no SunSpec spec documents them: treat the
private-field values as FoxESS-specific readings a firmware could still
change.

### Private blocks follow the model ticks

Each private block is tethered to the FoxESS model id whose registers
it holds (65000 DER info, 65004 energy statistics, 65006 battery, and
so on). A block is polled only while its model is ticked in the model
multi-select and the device serves it; untick a model and its blocks
stop being read. The datalogger serves roughly 3,150 private registers
across 35 blocks, and polling all of them every cycle costs about a
minute of relay exchanges, so a tick is what pays for the data.

Fresh setups pre-tick 64201, 64213, 65000 (generation and diagnostics),
65004 (the energy statistics: daily and lifetime PV, battery and AC
totals, self-use rate), 65006 (battery detail) and 65012 (the live
clock). The remaining blocks (65001 to 65037, a settings and debug
mirror) carry their fields as diagnostic entities, off by default;
tick the model and enable the entities you want. Installations that
were configured before the ticks gated the blocks keep their FoxESS
data through the version 3 config entry migration, which adds the new
default ids to the saved tick list once.

Where the same quantity shows up in several blocks the defaults pick
the best source: SoC and SoH come from 64213 (standard semantics), the
live battery power from 65004 (whole watts, cross-checked against the
rack voltage and current of 65006), the daily generation counter from
65000, and the lifetime PV and load counters from 65004, which 65000
does not carry.

## What this transport is

`POST /api/v1/sunspec/modbus_rw` accepts one raw Modbus RTU request
frame, hex encoded, in a JSON body, and answers with the matching RTU
response frame. The transport builds ordinary holding-register reads,
verifies the reply CRC, and refuses to decode anything it cannot fully
account for. Reads work; register writes refuse until the write path is
validated on hardware.

## Security notes

The datalogger's HTTP read API is effectively unauthenticated: it only
checks a username cookie that is not needed for reads, and the writes go
through the same interface. Keep the device on a LAN segment you trust,
firewall it if you can, and never forward its port 80 to the internet.
Your WiFi is an attack path too: the SSID password is the datalogger's
device password ("mtmt2020" from the factory).
