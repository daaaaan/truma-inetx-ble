# Truma LIN Protocol Decoding Progress

## Hardware Setup

- **Adapter**: LINTest-MI from BJT.cn (USB-LIN adapter)
- **USB baud**: 460800
- **LIN baud**: 9600
- **Connection**: Between iNet box and heater (not CP Plus)

## Adapter Protocol

The LINTest-MI uses a proprietary 16-byte frame protocol:

```
Byte 0:    Header (0x11=mode cmd, 0x44=monitor response)
Byte 1:    Channel
Byte 2:    Frame ID
Byte 3:    Direction
Byte 4:    Checksum type (0=error, 1=classic, 2=enhanced)
Byte 5:    Data length
Byte 6-13: Data payload (up to 8 bytes)
Byte 14:   LIN checksum
Byte 15:   Frame checksum (two's complement)
```

**Initialization sequence**:
1. Send mode command with mode=0 (standby)
2. Send mode command with mode=3 (monitor)

## Decoded Frame Mappings

### Frame 0x20 - Heater Status
| Byte | Field | Values |
|------|-------|--------|
| 0-1 | Counter/header | Changes frequently |
| 2 | Unknown | |
| 3 | Diesel flag | 250=ON, 0=OFF |
| 4 | Electric power | ×100 for watts (0=off, 9=900W, 18=1800W) |
| 5 | Operating status | >100=running (~210), <100=off (~2) |
| 6 | Mode flags | 240=on, 224=off |
| 7 | Unknown | Usually 0x0F |

### Frame 0x21 - Room & Water Status
| Byte | Field | Values |
|------|-------|--------|
| 0 | Counter | Changes frequently |
| 1 | Unknown | |
| 2 | Current room temp | ÷10 for °C (e.g., 193 = 19.3°C) |
| 3 | Current water temp | Direct °C value (e.g., 40 = 40°C) ✓ CONFIRMED |
| 4 | Unknown | |
| 5 | Water heater active | 49=OFF, other values=ON |
| 6-7 | Unknown | Usually 0xF0 0x0F |

### Frame 0x22 - Water Mode
| Byte | Field | Values |
|------|-------|--------|
| 0 | Counter/status | |
| 1 | Unknown | Often 240 or 112 |
| 2 | Water mode | 16=ECO/OFF, 17=COMFORT, 49=HOT |
| 3 | Status flags | |
| 4-7 | Padding | 0xFF |

### Frame 0x3C - Master Requests (Transport Layer)
| Field | Description |
|-------|-------------|
| NAD | Node address (0x7F=broadcast, 0x01=specific) |
| PCI | Protocol control info |
| SID | Service ID (0xB2=read, 0xB8=heartbeat) |
| Payload | Request data |

### Frame 0x3D - Slave Responses (Transport Layer)
| Field | Description |
|-------|-------------|
| NAD | Node address |
| PCI | Protocol control info |
| SID | Response ID (0xF2=read response, 0xF8=heartbeat response) |
| Payload | Response data including target temp |

**0x3D SID=0xF2 payload**:
- Byte 3: Target room temp (°C) - but shows stale value
- Byte 4: Unknown (often 70)
- Byte 5: Unknown (often 32)
- Byte 6: Energy mix (3=hybrid)

## Bus Behavior

- **Heater ON**: Continuous traffic (~3 frames/sec)
- **Heater OFF**: Bursts every ~15 seconds

## Decoded Fields - D4E / InetX (Protocol 4.0)

### Frame 0x20 - Heater Command (Master → Heater)
| Byte | Field | Encoding |
|------|-------|----------|
| 0 | **Room setpoint** | `temp = ((code - 170) mod 256) / 10` (0xAA=OFF) |
| 1 | Control flags | Bit0=heating enable, Bit7=water mode inv |
| 2 | Water setpoint | 0xAA=OFF, 0xC3=ECO(40°C), 0xCD=COMFORT, 0xD0=HOT(60°C) |
| 3 | Fuel control | 0xFA=enabled, 0x00=disabled |
| 4 | Electric power | ×100 for watts (0x00=off, 0x09=900W, 0x12=1800W) |
| 5 | Ventilation | Bits4-7: level (0xB=Eco, 0xD=High) |
| 6 | Constant | 0xE0 |
| 7 | Unknown | 0x0F |

**Room setpoint examples:**
- 0x40 = 15°C
- 0x4A = 16°C
- 0x72 = 20°C
- 0xA4 = 25°C (wraps at 256)

### Frame 0x21 - Heater Info 1 (Heater → Master)
| Byte | Field | Encoding |
|------|-------|----------|
| 0-2 | Temps (packed) | Two 12-bit values in Kelvin×10 |
| 3 | Burner power | ×100W |
| 4 | Electric power | ×100W |
| 5 | Status/fan | Bits0-1=energy, Bits4-6=fan speed |
| 6-7 | Unknown | Usually 0xF0 0x0F |

**Temperature decoding:**
- Room: `((byte1 & 0x0F) << 8 | byte0) / 10 - 273`
- Water: `((byte2 << 4) | (byte1 >> 4)) / 10 - 273`

## Where to Continue

### Priority 1: Room Setpoint ✓ SOLVED
- Frame 0x20 byte 0 contains room setpoint
- D4E formula: `temp = ((code - 170) mod 256) / 10`
- Confirmed by watching live changes: 0x40=15°C, 0x72=20°C, etc.

### Priority 2: Current Temperatures ✓ SOLVED
- Frame 0x21 bytes 0-2 contain 12-bit Kelvin×10 packed values
- Room: `((byte1 & 0x0F) << 8 | byte0) / 10 - 273`
- Water: `((byte2 << 4) | (byte1 >> 4)) / 10 - 273`

### Priority 3: Update decoder.py
- Update to use correct D4E/Protocol 4.0 formulas
- Remove old byte-offset based decoding

### Priority 3: Additional Status Fields
- 0x20 byte 2 changes with modes - meaning unclear
- 0x21 byte 4 - unknown, often ~18
- Error codes - not yet tested

## Files Created

- `src/lin/lintest_adapter.py` - LINTest-MI adapter driver
- `src/truma/decoder.py` - Truma protocol decoder

## Test Commands

```bash
# Quick status check
.venv/bin/python3 -c "
import sys
sys.path.insert(0, 'src')
from lin.lintest_adapter import LINTestAdapter
from truma.decoder import TrumaDecoder
import time

decoder = TrumaDecoder()
with LINTestAdapter('/dev/tty.usbmodem11101', lin_baud=9600) as adapter:
    for _ in range(50):
        for frame in adapter.read_frames():
            if frame.verify_checksum():
                decoder.decode_frame(frame.frame_id, frame.data)
        time.sleep(0.05)

s = decoder.status
print(f'Room: {s.current_room_temp}°C')
print(f'Heater: {\"ON\" if s.operating else \"OFF\"}')
print(f'Energy: {s.energy_mix.name if s.energy_mix else \"?\"} ({s.electric_power}W)')
print(f'Water: {s.water_mode.name if s.water_mode else \"?\"}')
"
```

Note: USB port may change between sessions (check with `ls /dev/tty.usb*`).
