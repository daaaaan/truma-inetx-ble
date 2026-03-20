# Truma iNet X BLE Protocol Reference
## Reverse-Engineered from Android APK v3.2.0

---

## 1. BLE UUIDs

All UUIDs share base suffix `-F3B2-11E8-8EB2-F2801F1B9FD1`.

### Advertising / Scan Filter Service UUIDs

| UUID | Panel Type |
|------|-----------|
| FC310000 | Legacy / unknown |
| FC310001 | Master panel |
| FC310002 | Room climate panel |
| FC310003 | Interface AC |
| FC310004 | Interface |
| FC310005 | Master facelift panel |
| FC310006 | Room climate facelift panel |
| FC316029 | BLE scan filter |

### Data Service UUID

`F47BBBAC-F3B2-11E8-8EB2-F2801F1B9FD1`

### GATT Characteristics

| UUID | Name | Direction |
|------|------|-----------|
| F47B0100 | SERVICE_READ | Notify (panel → phone) |
| F47B0101 | SERVICE_WRITE | Write (phone → panel) |
| FC314003 | DATA_READ | Notify (panel → phone) |
| FC314002 | DATA_WRITE | Write (phone → panel) |
| FC314001 | CMD_READ/WRITE | Notify + Write (bidirectional) |
| FC314004 | CMD_READ_ALT | Notify |

---

## 2. Protocol Stack

From bottom to top:

```
BLE GATT Characteristic
  └─ UartPackage (8-byte framing)
       └─ MuldexPackage (6-byte multiplexer)
            └─ TruMessageV3 (16-byte header)
                 └─ Sub-protocol (2-byte header: MBP/FMP/FUP/Secry/Reg/DD)
                      └─ CBOR payload
```

### UartPackage (8 bytes overhead)

| Offset | Size | Field | Value |
|--------|------|-------|-------|
| 0 | 1 | Start of Frame | `'S'` (0x53) |
| 1 | 1 | Frame Type | `'C'` = Control (0x43), `'D'` = Data (0x44) |
| 2 | 2 | Unique ID | UShort |
| 4 | 2 | Data Length | UShort |
| 6 | N | Data | Inner packet |

### MuldexPackage (6 bytes overhead)

| Offset | Size | Field | Value |
|--------|------|-------|-------|
| 0 | 2 | Endpoint | UShort LE, always 0 |
| 2 | 2 | Data Length | UShort LE |
| 4 | N | Data | TruMessageV3 bytes |
| 4+N | 2 | Padding | 0x0000 |

### TruMessageV3 (16-byte header)

| Offset | Size | Field |
|--------|------|-------|
| 0–1 | 2 | Destination Device ID (UShort LE) |
| 2–3 | 2 | Source Device ID (UShort LE) |
| 4–5 | 2 | Packet Size (UShort LE) = payload_len + 9 |
| 6 | 1 | Control Type byte |
| 7–15 | 9 | Segmentation Header |
| 16+ | N | Payload |

---

## 3. Control Types (byte at V3 offset 6)

| Value | Name | Purpose |
|-------|------|---------|
| 0x01 | DEVICE_REGISTRATION | Registration handshake |
| 0x02 | DEVICE_DISCOVERY | Device enumeration |
| 0x03 | MESSAGE_BROKER_PROTOCOL | Topic pub/sub (normal data) |
| 0x04 | FILE_MANAGER_PROTOCOL | Timer config, firmware files |
| 0x05 | SECURITY_PROTOCOL | Cloud tokens, certificates |
| 0x06 | FIRMWARE_UPDATE_PROTOCOL | OTA firmware update |
| 0x0A | NONE | Default / unset |

---

## 4. MBP Sub-Header (for Control Type 0x03)

Layout starting immediately after the TruMessageV3 header:

| Offset | Field |
|--------|-------|
| 0 | MBP Type byte |
| 1 | Correlation ID |
| 2+ | CBOR payload |

### MBP Types

| Value | Name | Direction |
|-------|------|-----------|
| 0x00 | INFO_MESSAGE | Panel → Phone (push update) |
| 0x01 | WRITE_MESSAGE | Phone → Panel (write parameter) |
| 0x02 | SUBSCRIBE_MESSAGE | Phone → Panel (subscribe to topics) |
| 0x03 | BINARY_MESSAGE | Bidirectional |
| 0x04 | PARAMETER_DISCOVERY_MESSAGE | Phone → Panel |
| 0x82 | SUBSCRIBE_RESPONSE | Panel → Phone |
| 0x84 | PARAMETER_DISCOVERY_RESPONSE | Panel → Phone |

---

## 5. Device Addresses

| Name | Decimal | Hex | Purpose |
|------|---------|-----|---------|
| messageBroker | 0 | 0x0000 | Message routing |
| panel | 257 | 0x0101 | Panel device |
| panelModel | 258 | 0x0102 | Panel model info |
| tinAventa | 514 | 0x0202 | TIN bus Aventa |
| ciTreiber | 1024 | 0x0400 | CI bus master |
| app | 1280 | 0x0500 | Phone / app |
| bleDevice | 1536 | 0x0600 | BLE master |
| blePeripheral | 1537 | 0x0601 | BLE peripheral |
| broadcast | 65535 | 0xFFFF | Broadcast |

---

## 6. CBOR Payload Models

### ParameterWrite (outbound command — phone → panel)

| CBOR key | Field | Type |
|----------|-------|------|
| `"tn"` | topicName | String |
| `"pn"` | parameter | String |
| `"v"` | value | Object |

### ParameterRead (inbound data — panel → phone)

| CBOR key | Field | Type |
|----------|-------|------|
| `"tn"` | topicName | String |
| `"pn"` | parameter | String |
| `"v"` | value | Object |
| `"type"` | type code | Long |
| `"perm"` | permission | Integer |
| `"avail"` | isAvailable | Integer (1 = yes) |
| `"min"` | minimum | Long |
| `"max"` | maximum | Long |
| `"enum"` | enum list | List of EnumElement |

### EnumElement

| CBOR key | Field | Type |
|----------|-------|------|
| `"n"` | name | String |
| `"a"` | available | boolean |
| `"v"` | value | int |

### SubscribeRequest (phone → panel)

| CBOR key | Field | Type |
|----------|-------|------|
| `"tn"` | topicNames | List\<String\> |

---

## 7. Connection Flow

1. **BLE Scan** — filter on service UUID FC316029, manufacturer ID 3187 (0x0C73).
2. **Connect** + MTU negotiation (517 bytes).
3. **Service discovery** — locate service F47BBBAC; enable NOTIFY on FC314003 and FC314001.
4. **Registration** — send RegistrationRequest with protocol version list `[5]`; receive assigned deviceId.
5. **Device Discovery** — broadcast request, receive device list.
6. **Parameter Discovery** — per-device; receive topic/parameter schemas.
7. **Topic Subscription** — batches of 10 topics with 250 ms delay between batches.
8. **Ongoing** — receive INFO_MESSAGE updates; send WRITE_MESSAGE commands.

---

## 8. Transport FSM (Data Transfer)

### Commands — phone → panel on CMD characteristic FC314001

| Command | OpCode | Purpose |
|---------|--------|---------|
| InitDataTransfer | 0x01 | Request to send N bytes |
| AckDataTransfer | 0xF0 | Acknowledge received data |

### Responses — panel → phone

| Response | OpCode | Purpose |
|----------|--------|---------|
| Announcement | 0x3F | Panel announcing incoming data |

### DataAckStatus

| Value | Meaning |
|-------|---------|
| 0x01 | TRANSFER_COMPLETED |
| 0x02 | NOT_READY |
| 0x03 | TOO_MUCH_DATA |
| 0x04 | TIMEOUT_ON_SEND |
| 0x05 | INTERNAL_ERROR |
| 0x06 | TIMEOUT_ON_RECEIVE |

---

## 9. Segmentation Header (9 bytes at V3 offsets 7–15)

### Byte 7 — flags

| Bit | Flag |
|-----|------|
| 0 | IS_SEGMENTED |
| 1 | MORE_SEGMENTS |
| 2 | CONTAINS_MESSAGE_SIZE |
| 3 | CONTAINS_OFFSET_SIZE |
| 4 | CONTAINS_SEGMENT_COUNT |
| 5 | SEGMENT_REQUEST |
| 6 | TRANSFER_ABORT |
| 7 | TRANSFER_FINISHED |

### Remaining bytes

| Offset | Size | Field |
|--------|------|-------|
| 8–9 | 2 | segment_number (UShort LE) |
| 10–11 | 2 | segment_count (UShort LE, if CONTAINS_SEGMENT_COUNT set) |
| 12–15 | 4 | message_size or offset_size (UInt LE) |

---

## 10. Topics and Parameters

**Temperature encoding: all values are tenths of °C (integer). 200 = 20.0 °C.**

### AirHeating (topic: `"AirHeating"`)

| Wire Key | Type | Description |
|----------|------|-------------|
| `"Active"` | Int | 0 = OFF, 1 = ACTIVE, 2 = IDLE |
| `"TgtTemp"` | Int | Target temp (tenths of °C) |
| `"Temp"` | Int | Current temp (read-only) |
| `"Mode"` | Int | Operating mode |
| `"ACC"` | Int | AC control state (0 / 1 / 2) |
| `"FanLevel"` | Int | Fan speed (1-indexed) |

### WaterHeating (topic: `"WaterHeating"`)

| Wire Key | Type | Description |
|----------|------|-------------|
| `"Active"` | Int | 0 = OFF, 1 = ACTIVE, 2 = IDLE |
| `"Mode"` | Int | Hot water mode (enum value) |
| `"BoostMode"` | Int | 0 = OFF, 1 = ON |
| `"FasterHeatingMode"` | Int | 0 = OFF, 1 = ON |
| `"FasterHeatingModeTime"` | Int | Duration in seconds |

### RoomClimate (topic: `"RoomClimate"`)

| Wire Key | Type | Description |
|----------|------|-------------|
| `"Active"` | Int | Climate system state |
| `"Mode"` | Int | 0=OFF, 1=ACC, 2=COOLING, 3=HEATING, 4=HEATING_AC, 5=VENTING, 6=DEHUMIDIFYING |
| `"TgtTemp"` | Int | Target temp (tenths of °C) |

### AirCooling (topic: `"AirCooling"`)

| Wire Key | Type | Description |
|----------|------|-------------|
| `"Active"` | Int | 0 = OFF, 1 = ACTIVE |
| `"TgtTemp"` | Int | Target temp (tenths of °C) |
| `"Temp"` | Int | Current temp (read-only) |
| `"Mode"` | Int | 0 = COMFORT, 1 = FAST |

### AirCirculation (topic: `"AirCirculation"`)

| Wire Key | Type | Description |
|----------|------|-------------|
| `"Active"` | Int | 0 = OFF, 5 = VENTING |
| `"FanLevel"` | Int | Fan speed |

### EnergySrc (topic: `"EnergySrc"`)

| Wire Key | Type | Description |
|----------|------|-------------|
| `"GasLevel"` | Int | 0 = OFF, 1 = ON |
| `"ElectricLevel"` | Int | 0 = OFF, 1 = ON |
| `"DieselLevel"` | Int | 0 = OFF, 1 = ON |
| `"EnergySourcePrio"` | Int | Priority selection |

### TimerConfig (topic: `"TimerConfig"`)

| Wire Key | Type | Description |
|----------|------|-------------|
| `"Timer1State"` – `"Timer6State"` | Int | 0 = disabled, 1 = enabled |
| `"Timer1"` – `"Timer6"` | Object | Timer config (id, name, symbol, start, end, weekDays) |
| `"TimerEnableCount"` | Int | Count of enabled timers |

**SymbolType (timer function):**

| Value | Name |
|-------|------|
| 0 | AUTOMATIC |
| 1 | AIR_HEATING |
| 2 | AIR_HEATING_AC |
| 3 | AIR_COOLING |
| 4 | AIR_CIRCULATION |
| 5 | AIR_DEHUMID |
| 6 | WATER_HEATING |

### Switches (topic: `"Switches"`)

28 binary fields (0 = OFF, 1 = ON):

`FreshWaterPump`, `ExternalLights`, `ExternalLights2`, `InternalLights`, `TV`, `Radio`, `FloorHeating`, `Multimedia`, `NightLight`, `FWHeatingMode`, `GWHeatingMode`, `Inverter`, `12VSockets`, `GPOutput1`–`GPOutput5`, `Light1`–`Light6`, `DoorStep`, `FreshWaterValve`, `GreyWaterValve`, `StoveIgnition`

### AmbientLight (topic: `"AmbientLight"`)

| Wire Key | Type | Description |
|----------|------|-------------|
| `"Active"` | Int | 0 = OFF, 1 = ON |
| `"LightStep"` | Int | Brightness level |

### Other Topics (schema not fully decoded)

`GasBtl`, `GasControl`, `PowerMgmt`, `PowerSupply`, `Temperature`, `VBat`, `L1Bat`, `L2Bat`, `LinePower`, `FreshWater`, `GreyWater`, `BatteryMngmt`, `Panel`, `System`, `Connect`, `BleDeviceManagement`, `MobileIdentity`, `BluetoothDevice`, `Resources`, `Identify`, `Transfer`, `Install`, `DeviceManagement`, `ErrorReset`

---

## 11. Global Enums

**ControlLoopStateEnum** (used by `Active` fields):

| Value | Name |
|-------|------|
| 0 | OFF |
| 1 | ACTIVE |
| 2 | IDLE |

**ModeType** (RoomClimate `Mode`):

| Value | Name |
|-------|------|
| 0 | OFF |
| 1 | ACC |
| 2 | COOLING |
| 3 | HEATING |
| 4 | HEATING_AC |
| 5 | VENTING |
| 6 | DEHUMIDIFYING |

**FanModeType**:

| Value | Name |
|-------|------|
| 0 | COMFORT |
| 1 | FAST |

**DeviceLocationEnum**:

| Value | Name |
|-------|------|
| 0 | TOP |
| 1 | BOTTOM (default) |
| 2 | FRONT |
| 3 | REAR |

**ConnStateEnum**:

| Value | Name |
|-------|------|
| 0 | STANDBY |
| 1 | LIVE |
| 2 | DISCONNECTED |

**PowerModeEnum**:

| Value | Name |
|-------|------|
| 0 | SHUTDOWN |
| 1 | STANDBY |
| 2 | TURN_OFF_12V |
| 3 | ON |
| 4 | PREPARING_SHUTDOWN |
| 5 | PREPARING_STANDBY |
| 6 | READY_FOR_SHUTDOWN |

**PairingFlagEnum**:

| Value | Name |
|-------|------|
| 0x01 | PairingEnabled |
| 0x00 | PairingDisabled |
| 0xF0 | PairingFailed |

**DeviceTypeEnum**:

| Value | Name |
|-------|------|
| 0 | UNKNOWN |
| 1 | PANEL |
| 2 | HEATER |
| 3 | AC |
| 4 | VEHICLE_CONNECT |
| 5 | APP |
| 6 | BATTERY_SENSOR |
| 7 | DEMO |
| 8 | DIAGNOSIS |
| 9 | GAS_SENSOR |
| 10 | INTERFACE |
| 11 | CONNECT |
| 12 | BLE_REMOTE_CONTROL |

---

## 12. CRC Algorithm

CRC-32/MPEG-2 used for firmware integrity checks:

| Parameter | Value |
|-----------|-------|
| Poly | 0x04C11DB7 |
| Init | 0xFFFFFFFF |
| RefIn | false |
| RefOut | false |
| XorOut | 0x00000000 |

---

## 13. Constants

| Constant | Value |
|----------|-------|
| TRUMA_MANUFACTURER_ID | 3187 (0x0C73) |
| VENDOR_ID | 17943 (0x4617) |
| PRODUCT_ID | 832 (0x0340) |
| MTU_VALUE | 517 |
| SCAN_REPORT_DELAY | 1200 ms |
| RETRY_CONNECT_COUNT | 3 |
| CONNECTION_DELAY | 300 ms |
| MAX_TOPICS_PER_SUBSCRIBE | 10 |
| SUBSCRIBE_BATCH_DELAY | 250 ms |
| WRITE_RATE_LIMIT | 10 msg/s |
| WRITE_BUFFER_MAX | 100 |
| NOTIFY_READ_TIMEOUT | 10 s |
| LATEST_PROTOCOL_VERSION | 5.1 |

---

## 14. Complete Topic Name Registry

```
Identify
Transfer
Install
DeviceManagement
AirHeating
WaterHeating
AirCirculation
EnergySrc
PowerSupply
PowerMgmt
GasControl
Switches
Temperature
ErrorReset
VBat
L1Bat
L2Bat
LinePower
FreshWater
GreyWater
RoomClimate
AirCooling
AmbientLight
GasBtl
BleDeviceManagement
Panel
BatteryMngmt
MobileIdentity
Connect
TimerConfig
BluetoothDevice
System
Resources
```

---

## 15. Real Device Validation

Findings from direct BLE communication with a physical Truma unit.

### Device Identity

| Field | Value |
|-------|-------|
| Model | Combi D 4 E GEN2 |
| Serial | CBY04EU-F-36063191 |
| Firmware | 2.3 |
| SupID | 17943 |

### Discovered Devices

| Address (hex) | Type | Instance | Description |
|---------------|------|----------|-------------|
| 0x0101 | PANEL | #1 | iNet X Panel |
| 0x0200 | TIN_MASTER | #0 | TIN bus master |
| 0x0201 | TIN_MASTER | #1 | Combi D 4 E heater (sends temp data, receives commands) |
| 0x032C | CAN_MASTER | #44 | CAN device |
| 0x0400 | CI_BUS_MASTER | #0 | CI bus master |
| 0x0500 | BLE_APP_SLAVE | #0 | Phone app slot 0 |
| 0x0501 | BLE_APP_SLAVE | #1 | Phone app slot 1 (our assigned address) |
| 0x0600 | BLE_MASTER | #0 | BLE master (panel BLE controller) |
| 0x0601 | BLE_MASTER | #1 | BLE peripheral |
| 0x0800 | CAN_SLAVE | #0 | CAN slave |
| 0x0902 | VIRTUAL | #2 | Virtual device |
| 0x0A01 | HMI | #1 | Panel display/UI |

### Real Parameter Values

Values confirmed from parameter discovery on a live device.

| Topic | Parameter | Range / Enum |
|-------|-----------|--------------|
| RoomClimate | Mode | enum: Off=0, Heating=3, Ventilating=5 |
| RoomClimate | TgtTemp | 16.0–30.0 °C (wire: 160–300) |
| AirHeating | TgtTemp | 5.0–30.0 °C (wire: 50–300) |
| AirHeating | Mode | enum: Fast=0, Comfort=1 |
| AirHeating | Temp | current room temp, −40 to 60 °C |
| AirCirculation | FanLevel | 0–10 |
| WaterHeating | Mode | enum: 40°C=0, 60°C=1, 70°C=2 |
| WaterHeating | Temp | −40 to 130 °C |
| EnergySrc | DieselLevel | enum: Diesel off=0, Diesel on=1 |
| EnergySrc | ElectricLevel | enum: Electric off=0, 900W=1, 1800W=2 |
| Panel | Intst | brightness 10–100 |
| Panel | DisplayTimeout | seconds |

### New Topics Not in APK

Discovered from real-device parameter discovery; not present in the decompiled APK.

| Topic | Parameters | Notes |
|-------|------------|-------|
| Blemcu | BtDeviceName, BtPin, BleEnablePairing, BleStatus, BleConnState | BLE MCU status |
| Eol | Vcc12, Vcc5 | End-of-line voltages (observed: Vcc12=12.361 V, Vcc5=5.069 V) |
| ACCAirHeating | Mode | ACC mode (min=1, max=1) |
| TimeAndDate | Date, Time | Panel date/time (string values) |

### Critical Implementation Notes

Lessons learned from live testing.

**BLE / macOS**
- CCCD must be force re-written (disable then enable) on macOS CoreBluetooth to receive notifications reliably.
- macOS CLI tools require an app bundle with `NSBluetoothAlwaysUsageDescription` in `Info.plist`.
- Do NOT subscribe to FC314004 (CMD_ALT) — doing so causes transport failure.

**Transport / ACK flow**
- Registration returns a dynamic `addr` field — use that value as the source address for all subsequent messages.
- Auto-ACK every inbound DATA_R frame with opcode `f001`.
- Auto-confirm every `83xx00` MsgAck with response `0300`.
- MsgAck (`83xx00`) arrives asynchronously — do not block the send path waiting for it.
- No explicit keepalive is needed — the constant ACK exchange keeps the connection alive.

**Command routing**
- Commands for `RoomClimate` must be sent to the panel (destination 0x0101).
- Commands for `AirHeating` and `WaterHeating` must be sent to the heater (destination 0x0201).

### Command Format (from capture)

```
CBOR payload:  {"tn": "RoomClimate", "pn": "Mode", "v": 3, "id": 0}
V3 Frame:      dest=0x0101, src=<assigned_addr>, ctrl=0x03, sub=0x01, corr=0
```
