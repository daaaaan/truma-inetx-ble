# Truma InetX Home Assistant Integration - Design

## Overview

Two-phase approach:
1. **Protocol Analysis** - Python-based LIN sniffer to decode InetX traffic
2. **Production Integration** - ESPHome on ESP32 with native HA integration + local web UI

## System Details

- **Truma Model:** Combi heater (diesel/electric with hot water)
- **Heating modes:** Diesel, Electric (900W/1800W), Hybrid
- **Protocol:** LIN bus (Local Interconnect Network)
- **Analysis Hardware:** USB-LIN adapter + Raspberry Pi (temporary)
- **Production Hardware:** ESP32 + LIN transceiver
- **Integration:** ESPHome (native HA + local web server)

## Architecture

### Phase 1: Protocol Analysis (Temporary)
```
┌─────────────┐    LIN     ┌──────────────┐
│ Truma Combi │◄──────────►│ Control Panel│
└─────────────┘            └──────────────┘
                                  │
                                  │ T-splice (passive sniff)
                                  ▼
                           ┌──────────────┐
                           │ USB-LIN      │
                           │ Adapter      │
                           └──────────────┘
                                  │ USB
                                  ▼
                           ┌──────────────┐
                           │ Raspberry Pi │ → Log files for analysis
                           │ (Python)     │
                           └──────────────┘
```

### Phase 2: Production (ESPHome)
```
┌─────────────┐    LIN     ┌──────────────┐    LIN     ┌─────────────┐
│ Truma Combi │◄──────────►│ Control Panel│◄──────────►│    ESP32    │
└─────────────┘            └──────────────┘            │  (ESPHome)  │
                                                       └──────┬──────┘
                                                              │ WiFi
                                    ┌─────────────────────────┼─────────────────────────┐
                                    │                         │                         │
                                    ▼                         ▼                         ▼
                             ┌──────────────┐          ┌──────────────┐          ┌──────────────┐
                             │Home Assistant│          │  Local Web   │          │    Phone     │
                             │   (native)   │          │   Server     │          │   Browser    │
                             └──────────────┘          └──────────────┘          └──────────────┘
```

## LIN Frame Structure

```
┌───────┬────────┬──────────────────┬──────────┐
│ Break │ Sync   │ ID (6 bits +     │ Data     │
│ 13bit │ 0x55   │ 2 parity bits)   │ 0-8 bytes│ + Checksum
└───────┴────────┴──────────────────┴──────────┘
```

- **Baud rate:** 9600 (confirmed from existing projects)
- **Stop bits:** 2
- **Checksum:** Enhanced (LIN 2.x style)

## Project Structure

```
vanlin/
├── src/
│   ├── lin/
│   │   ├── __init__.py
│   │   ├── adapter.py      # USB-LIN adapter communication
│   │   ├── frame.py        # LIN frame parsing/building
│   │   └── sniffer.py      # Raw traffic capture & logging
│   ├── truma/
│   │   ├── __init__.py
│   │   ├── decoder.py      # Truma-specific frame decoding
│   │   └── commands.py     # Truma command building (Phase 4)
│   ├── mqtt/
│   │   ├── __init__.py
│   │   └── publisher.py    # HA MQTT publishing with auto-discovery
│   └── main.py             # Entry point & orchestration
├── tools/
│   └── raw_logger.py       # Simple CLI tool for sniffing (Phase 1)
├── logs/                   # Captured LIN traffic logs
├── docs/
│   ├── plans/
│   └── protocol.md         # Reverse-engineered protocol notes
├── tests/
├── pyproject.toml
└── README.md
```

## Dependencies

- `pyserial` - USB-LIN adapter communication
- `paho-mqtt` - MQTT publishing
- `structlog` - Structured logging for traffic analysis

## Home Assistant Entities

```
climate.truma_combi           # Main heating (target temp, on/off)
sensor.truma_room_temp        # Current room temperature
sensor.truma_water_temp       # Hot water temperature
binary_sensor.truma_flame     # Diesel burner active
binary_sensor.truma_element   # Electric element active

select.truma_heating_mode     # "off" / "diesel" / "electric" / "hybrid"
select.truma_electric_power   # "900W" / "1800W" (when electric/hybrid)
select.truma_water_mode       # "off" / "eco" / "hot"
```

## MQTT Topics

```
homeassistant/climate/truma_combi/config     # HA auto-discovery
homeassistant/sensor/truma_combi_*/config    # Individual sensors

truma/combi/status                           # JSON state updates
truma/combi/command                          # Control commands (Phase 4)
```

## Development Phases

### Phase 1: Sniff & Log (Python)
- Capture raw LIN traffic with USB-LIN adapter
- Identify frame IDs and patterns
- Verify InetX uses same protocol as CP Plus

### Phase 2: Decode (Python)
- Map frames to meaning (temps, status, commands)
- Compare with existing inetbox.py/ESPHome component
- Document any InetX-specific differences

### Phase 3: ESPHome Integration
- Test existing esphome-truma_inetbox component
- If works: configure for InetX
- If not: fork and adapt based on Phase 2 findings

### Phase 4: Production Deployment
- ESP32 + LIN transceiver hardware build
- ESPHome config with:
  - Native Home Assistant API
  - Local web server for standalone control
  - WiFi fallback AP for initial setup

## Log Format

```
2024-12-13T21:15:32.123 ID=0x12 DATA=01 A5 00 00 1C 00 00 00 CHK=B7
```

Designed for human readability and machine parsing during reverse-engineering.

## Prior Art

Existing reverse-engineering work exists for Truma CP Plus:
- [inetbox.py](https://github.com/danielfett/inetbox.py) - Python reference implementation
- [esphome-truma_inetbox](https://github.com/Fabian-Schmidt/esphome-truma_inetbox) - ESPHome component (target)
- [TruMinus](https://github.com/olivluca/TruMinus) - ESP32 CP Plus emulator
- [inetbox2mqtt](https://github.com/mc0110/inetbox2mqtt) - MQTT bridge

InetX is "iNet ready" so protocol should be compatible.

## ESPHome Configuration (Phase 3-4)

Target configuration for production:

```yaml
esphome:
  name: truma-inetx
  friendly_name: Truma Heater

esp32:
  board: esp32dev

wifi:
  ssid: !secret wifi_ssid
  password: !secret wifi_password
  ap:
    ssid: "Truma-Fallback"
    password: !secret fallback_password

web_server:
  port: 80
  local: true

api:
  encryption:
    key: !secret api_key

logger:
  level: DEBUG

uart:
  tx_pin: GPIO17
  rx_pin: GPIO16
  baud_rate: 9600
  stop_bits: 2

external_components:
  - source: github://Fabian-Schmidt/esphome-truma_inetbox

truma_inetbox:
  id: truma

climate:
  - platform: truma_inetbox
    name: "Room"
    type: ROOM
  - platform: truma_inetbox
    name: "Water"
    type: WATER

sensor:
  - platform: truma_inetbox
    name: "Current Room Temp"
    type: CURRENT_ROOM_TEMPERATURE
  - platform: truma_inetbox
    name: "Current Water Temp"
    type: CURRENT_WATER_TEMPERATURE

binary_sensor:
  - platform: truma_inetbox
    name: "Heater Active"
    type: HEATER_ROOM
  - platform: truma_inetbox
    name: "Water Heating"
    type: HEATER_WATER

select:
  - platform: truma_inetbox
    name: "Energy Mix"
    type: ENERGY_MIX
  - platform: truma_inetbox
    name: "Electric Power"
    type: ELECTRIC_POWER_LEVEL
```

## Hardware for ESPHome

- ESP32 dev board (ESP32-WROOM-32 recommended)
- LIN transceiver module (TJA1020 or MCP2003)
- 12V power supply (from vehicle)
- 3.3V regulator for ESP32 (if not on dev board)

## Success Criteria (Phase 1)

- [ ] See valid LIN frames on the wire
- [ ] Identify which frame IDs are Truma Combi traffic
- [ ] Decode at least one value (likely room temperature)
