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
