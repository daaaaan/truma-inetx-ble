# Truma InetX Home Assistant Integration - Design

## Overview

Python-based LIN bus sniffer/controller running on a Raspberry Pi that publishes Truma Combi heater status to Home Assistant via MQTT, with eventual control capabilities.

## System Details

- **Truma Model:** Combi heater (diesel/electric with hot water)
- **Heating modes:** Diesel, Electric (900W/1800W), Hybrid
- **Protocol:** LIN bus (Local Interconnect Network)
- **Hardware:** USB-LIN adapter + Raspberry Pi
- **Integration:** MQTT with Home Assistant auto-discovery

## Architecture

```
┌─────────────┐    LIN     ┌──────────────┐    LIN     ┌─────────────┐
│ Truma Combi │◄──────────►│ Control Panel│◄──────────►│ Other nodes │
└─────────────┘            └──────────────┘            └─────────────┘
                                  │
                                  │ T-splice
                                  ▼
                           ┌──────────────┐
                           │ USB-LIN      │
                           │ Adapter      │
                           └──────────────┘
                                  │ USB
                                  ▼
                           ┌──────────────┐     MQTT    ┌──────────────┐
                           │ Raspberry Pi │────────────►│Home Assistant│
                           │ (Python)     │             │              │
                           └──────────────┘             └──────────────┘
```

## LIN Frame Structure

```
┌───────┬────────┬──────────────────┬──────────┐
│ Break │ Sync   │ ID (6 bits +     │ Data     │
│ 13bit │ 0x55   │ 2 parity bits)   │ 0-8 bytes│ + Checksum
└───────┴────────┴──────────────────┴──────────┘
```

- **Baud rate:** Likely 19200 (to verify)
- **Checksum:** Classic or Enhanced (to verify)

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

### Phase 1: Sniff & Log
- Capture raw LIN traffic
- Identify frame IDs and patterns
- Verify hardware/baud rate/checksum

### Phase 2: Decode
- Map frames to meaning (temps, status, commands)
- Document protocol findings

### Phase 3: Monitor
- Publish decoded status to HA via MQTT
- Set up auto-discovery

### Phase 4: Control
- Send commands to Truma (once protocol is understood)
- Implement full climate entity control

## Log Format

```
2024-12-13T21:15:32.123 ID=0x12 DATA=01 A5 00 00 1C 00 00 00 CHK=B7
```

Designed for human readability and machine parsing during reverse-engineering.

## Prior Art

Existing reverse-engineering work exists for Truma CP Plus - protocol structure may be similar. InetX-specific protocol is undocumented and will require sniffing.

## Success Criteria (Phase 1)

- [ ] See valid LIN frames on the wire
- [ ] Identify which frame IDs are Truma Combi traffic
- [ ] Decode at least one value (likely room temperature)
