# Truma iNetX BLE Controller for Cerbo GX

Control a Truma Combi heater via Bluetooth Low Energy from a Victron Cerbo GX, with full Home Assistant integration.

Protocol documented through BLE traffic analysis and interoperability testing against a real Truma Combi D 4 E (GEN2) with iNetX panel.

## What This Does

```
Truma iNetX Panel  <--BLE-->  Cerbo GX  --MQTT-->  Home Assistant
                                |
                             REST API (:8090)
```

- Connects to the Truma iNetX panel over BLE using the BlueZ D-Bus API (no bleak needed on the Cerbo)
- Decodes the proprietary CBOR-over-BLE protocol (TruMessageV3)
- Publishes heater state to MQTT with Home Assistant auto-discovery
- Accepts commands from HA (temperature, heating mode, water heating, energy source)
- Provides a local REST API on port 8090 for direct control
- Auto-reconnects on BLE disconnection with exponential backoff

## Home Assistant Entities

Once running, these entities appear automatically in HA:

| Entity | Type | Description |
|--------|------|-------------|
| Truma Room Heating | Climate | Mode (off/heat/fan), target temp, current temp |
| Truma Water Heating Mode | Select | Off / 40C / 60C / 70C |
| Truma Room Temperature | Sensor | Current room temp (C) |
| Truma Water Temperature | Sensor | Current water temp (C) |
| Truma Internal Temperature | Sensor | Heater internal temp (C) |
| Truma Diesel Heating | Switch | Diesel burner on/off |
| Truma Electric Heating | Select | Off / 900W / 1800W |
| Truma Fan Level | Number | Fan speed 0-10 |
| Truma Flame | Binary Sensor | Burner flame active |
| Truma BLE Connected | Binary Sensor | BLE connection status |

## Hardware Requirements

- **Truma Combi heater** with iNetX BLE panel (tested with Combi D 4 E GEN2)
- **Victron Cerbo GX** (or other Venus OS device)
- **USB Bluetooth dongle** — the Cerbo's onboard BCM Bluetooth has issues with BLE GATT. A RTL8761BU-based dongle on `hci1` works reliably.
- **MQTT broker** accessible from the Cerbo (e.g., Mosquitto on your network)

## Installation on Cerbo GX

### 1. Install dependencies

```bash
opkg update
opkg install python3-pip
pip3 install dbus-fast cbor2 paho-mqtt
```

### 2. Deploy the service

```bash
# Copy to Cerbo
scp -r data/dbus-truma root@cerbo:/data/dbus-truma

# Create daemontools service link
ln -s /data/dbus-truma /service/dbus-truma
```

### 3. Pair the Truma

Before the service can connect, you need to pair with the iNetX panel once:

```bash
# On the Cerbo, run the pairing script with the 6-digit passkey
# shown on the Truma panel (Menu > Settings > Bluetooth > Pair)
python3 scripts/ble_pair.py 123456
```

The pairing is persisted by BlueZ — subsequent connections happen automatically.

### 4. Configure MQTT

Edit `data/dbus-truma/service/mqtt_ha.py` and set your MQTT broker address:

```python
MQTT_HOST = "192.168.1.55"  # Your MQTT broker IP
MQTT_PORT = 1883
```

### 5. Start the service

```bash
svc -u /service/dbus-truma
```

Check logs:
```bash
svlogd -tt /service/dbus-truma/log/main/current
# or
tail -f /var/log/dbus-truma/current
```

## REST API

The service exposes a REST API on port 8090:

```bash
# Get current status
curl http://cerbo:8090/status

# Set room heating to 22C
curl -X POST http://cerbo:8090/command \
  -H 'Content-Type: application/json' \
  -d '{"topic": "RoomClimate", "param": "TgtTemp", "value": 220}'

# Turn on heating mode
curl -X POST http://cerbo:8090/command \
  -H 'Content-Type: application/json' \
  -d '{"topic": "RoomClimate", "param": "Mode", "value": 3}'

# Set water heating to 60C
curl -X POST http://cerbo:8090/command \
  -H 'Content-Type: application/json' \
  -d '{"topic": "WaterHeating", "param": "Mode", "value": 1}'
```

### Command Reference

| Topic | Parameter | Values | Description |
|-------|-----------|--------|-------------|
| RoomClimate | Mode | 0=off, 3=heating, 5=fan | Room climate mode |
| RoomClimate | TgtTemp | 160-300 | Target temp (tenths of C, e.g. 220 = 22.0C) |
| AirHeating | TgtTemp | 50-300 | Air heating target (tenths of C) |
| WaterHeating | Mode | 0=40C, 1=60C, 2=70C | Water temperature preset |
| WaterHeating | Active | 0/1 | Water heater on/off |
| EnergySrc | DieselLevel | 0/1 | Diesel burner on/off |
| EnergySrc | ElectricLevel | 0=off, 1=900W, 2=1800W | Electric heating level |
| AirCirculation | FanLevel | 0-10 | Fan speed |

## BLE Protocol Overview

The Truma iNetX uses a layered protocol over BLE GATT:

```
BLE GATT Characteristic
  -> Transport Layer (InitDataTransfer handshake)
    -> TruMessageV3 (16-byte header: dest, src, control type)
      -> Sub-protocol (MBP: subscribe, write, info, param discovery)
        -> CBOR payload (topic/parameter/value)
```

### GATT Characteristics

| UUID suffix | Name | Direction |
|-------------|------|-----------|
| `fc314001` | CMD | Read/Write — transport control |
| `fc314002` | DATA_WRITE | Write — message payload |
| `fc314003` | DATA_READ | Notify — incoming messages |
| `fc314004` | CMD_ALT | Notify — do NOT subscribe (causes issues) |

### Connection Sequence

1. **BLE Connect** + service discovery
2. **Subscribe** to notifications on CMD (`4001`) and DATA_READ (`4003`)
3. **Register** — send `{pv: [5,1]}` to broadcast, receive assigned device address
4. **Subscribe topics** — 33 topics in 4 batches of 10
5. **Send identity** — SystemTime, MobileIdentity (Muid/Uuid persisted for reconnection)
6. **Parameter discovery** — request current values from heater (`0x0201`) and panel (`0x0101`)
7. **Listen** — receive INFO_MESSAGE updates with current state

### Transport Handshake

Every message uses a 5-step transport handshake:

```
App  -> CMD:  01 <len_lo> <len_hi>   (InitDataTransfer)
App  <- CMD:  81 00                   (ReadyStatus)
App  -> DATA: <full message>          (send payload)
App  <- CMD:  f0 01                   (AckDataTransfer)
App  <- CMD:  83 xx 00                (MsgAck with ID)
App  -> CMD:  03 00                   (confirm receipt)
```

### Identity Persistence

The iNetX panel remembers paired clients by their Muid/Uuid. After initial pairing, you must reuse the same identity — stored in `.truma_identity.json`. Connecting with a new identity after pairing will be rejected.

## Project Structure

```
data/dbus-truma/           # Cerbo GX service (deploy this)
  run                      # daemontools run script
  service/
    main.py                # Service orchestrator
    ble_transport.py       # BLE connection via BlueZ D-Bus
    protocol.py            # V3 frame builder/parser
    truma_state.py         # State model + command validation
    mqtt_ha.py             # MQTT + HA auto-discovery
    dbus_service.py        # Venus OS D-Bus integration
    rest_api.py            # HTTP API on port 8090
    const.py               # UUIDs, device addresses, topic lists

scripts/                   # Development & testing tools
  ble_pair.py              # BLE pairing via BlueZ agent (run on Cerbo)
  truma_control.py         # CLI controller using bleak (dev machine)
  truma_dbus.py            # dbus-fast controller (Cerbo, standalone)
  test_protocol.py         # Protocol validation against real device
  ble_bridge.py            # BLE MITM proxy for traffic capture

docs/
  truma-inetx-protocol-reference.md   # Full protocol spec from traffic analysis
```

## Development

The `scripts/` directory contains standalone tools used during development:

- **`truma_control.py`** — Full CLI controller using bleak (macOS/Linux desktop). Connect, monitor, send commands.
  ```bash
  python3 scripts/truma_control.py --monitor          # live status
  python3 scripts/truma_control.py --heat on --temp 20  # set heating
  ```

- **`test_protocol.py`** — Protocol verification tool. Runs registration, subscription, identity, and listening tests against a real device using dbus-fast.

- **`ble_bridge.py`** — BLE MITM proxy. Sits between the Truma app and the real iNetX to capture and log all traffic.

## Protocol Documentation

See [`docs/truma-inetx-protocol-reference.md`](docs/truma-inetx-protocol-reference.md) for the complete protocol specification documented through BLE traffic analysis, including:

- Full GATT UUID mapping
- Protocol stack layers (UartPackage, MuldexPackage, TruMessageV3)
- All control types and MBP sub-protocols
- Device address assignments
- CBOR payload formats

## License

This project is not affiliated with Truma or Victron. Use at your own risk.
