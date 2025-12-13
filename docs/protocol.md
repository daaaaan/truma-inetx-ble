# Truma LIN Protocol Reference

Based on reverse-engineering work from existing open-source projects.

## Sources

- [inetbox.py](https://github.com/danielfett/inetbox.py) - Python iNet box emulator
- [esphome-truma_inetbox](https://github.com/Fabian-Schmidt/esphome-truma_inetbox) - ESPHome component
- [TruMinus](https://github.com/olivluca/TruMinus) - ESP32 CP Plus emulator
- [inetbox2mqtt](https://github.com/mc0110/inetbox2mqtt) - MQTT bridge

## Communication Parameters

| Parameter   | Value   | Notes                               |
|-------------|---------|-------------------------------------|
| Baud rate   | 9600    | NOT 19200 as typical for automotive |
| Stop bits   | 2       |                                     |
| Data bits   | 8       |                                     |
| Parity      | None    |                                     |
| Checksum    | Enhanced| LIN 2.x style (PID + data)          |

## Frame Identifiers (PIDs)

### Status Messages (Slave → Master)

| PID  | Description                                    |
|------|------------------------------------------------|
| 0x18 | iNet box status - bit 0 indicates command ready|
| 0x20 | Display status update 1                        |
| 0x21 | Display status update 2                        |
| 0x22 | Display status update 3                        |

### Transport Layer

| PID  | Description                    |
|------|--------------------------------|
| 0x3C | Master request frame           |
| 0x3D | Slave response frame           |

## Service IDs (SIDs) - Transport Layer

| SID  | Direction      | Description              |
|------|----------------|--------------------------|
| 0xB0 | Master → Slave | Network address assignment|
| 0xB9 | Master → Slave | Heartbeat/keep-alive     |
| 0xBA | Master → Slave | Data upload request      |
| 0xBB | Master → Slave | Data download            |

## Status Buffer Format

Settings are exchanged via 10-byte preamble followed by ID bytes:

| ID bytes    | Direction      | Content                    |
|-------------|----------------|----------------------------|
| 0x14, 0x33  | CP Plus → iNet | Current settings/status    |
| 0x0C, 0x32  | iNet → CP Plus | Settings modifications     |

## Temperature Values

### Room Temperature
- 0 = Off
- 5-30 = Target temperature in °C

### Water Temperature
- 0 = Off
- 40 = Eco mode
- 60 = Normal/Hot
- 200 = Boost mode

## Heating Modes

| Value | Mode      |
|-------|-----------|
| 0     | Off       |
| 1     | Eco       |
| 2     | High      |

## Energy Mix (Diesel/Electric Combi)

| Value | Mode        |
|-------|-------------|
| 0     | None/Off    |
| 1     | Gas/Diesel  |
| 2     | Electricity |
| 3     | Mix/Hybrid  |

## Electric Power Levels

| Value | Power  |
|-------|--------|
| 0     | Off    |
| 900   | 900W   |
| 1800  | 1800W  |

## Hardware Notes

### LIN Transceiver Connection
- Connect LIN wire to vehicle LIN bus
- GND to vehicle chassis ground
- 12V supply (same as Combi/CP Plus)
- UART TX/RX to microcontroller

### RJ12 Connector Pinout
The Truma system uses RJ12 (6P6C) connectors:
- Can tap into existing connection between Combi and CP Plus using a splitter

## InetX vs CP Plus

The InetX is essentially an "iNet ready" version of the system. The protocol should be largely compatible with existing inetbox.py work, but may have additional frame IDs or features.

## Reverse Engineering Notes

When sniffing traffic, look for:
1. PIDs 0x20-0x22 for regular status updates
2. PID 0x18 for iNet box presence detection
3. Temperature changes should appear in status frames

### Test Scenarios
- Idle system: baseline traffic pattern
- Change target temp: watch for 0x20-0x22 changes
- Change heating mode: energy mix byte should change
- Turn on/off: observe status transitions
