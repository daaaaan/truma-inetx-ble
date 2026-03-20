#!/usr/bin/env python3
"""Test Truma BLE protocol against APK reverse-engineering findings.

Connects to Truma iNetX via dbus-fast and logs raw byte-level
data to validate the protocol stack discovered from the APK.
"""

import asyncio
import struct
import cbor2
import json
import time
from pathlib import Path
from dbus_fast.aio import MessageBus
from dbus_fast import BusType, Variant  # noqa

IDENTITY_FILE = Path(__file__).parent / ".truma_identity.json"

# APK-confirmed UUIDs
DATA_SERVICE = "f47bbbac-f3b2-11e8-8eb2-f2801f1b9fd1"
CHAR_CMD     = "fc314001-f3b2-11e8-8eb2-f2801f1b9fd1"  # CMD R/W (transport control)
CHAR_DATA_W  = "fc314002-f3b2-11e8-8eb2-f2801f1b9fd1"  # DATA write
CHAR_DATA_R  = "fc314003-f3b2-11e8-8eb2-f2801f1b9fd1"  # DATA read (notify)
CHAR_CMD_ALT = "fc314004-f3b2-11e8-8eb2-f2801f1b9fd1"  # CMD alt (notify)

# APK-confirmed device addresses
DEV_MSG_BROKER = 0x0000
DEV_PANEL      = 0x0101
DEV_APP        = 0x0500
DEV_BROADCAST  = 0xFFFF

# APK-confirmed control types
CTRL_REGISTRATION = 0x01
CTRL_DISCOVERY    = 0x02
CTRL_MBP          = 0x03

# APK-confirmed MBP types
MBP_INFO       = 0x00
MBP_WRITE      = 0x01
MBP_SUBSCRIBE  = 0x02
MBP_PARAM_DISC = 0x04

BLUEZ = "org.bluez"


def load_identity():
    if IDENTITY_FILE.exists():
        with open(IDENTITY_FILE) as f:
            return json.load(f)
    import uuid as _uuid
    identity = {
        "muid": str(_uuid.uuid4()).upper(),
        "uuid": str(_uuid.uuid4()).lower(),
        "username": "Vanlin Controller"
    }
    with open(IDENTITY_FILE, 'w') as f:
        json.dump(identity, f, indent=2)
    return identity


def build_v3_frame(dest, src, control_type, mbp_type, correlation_id, cbor_payload):
    """Build a proper TruMessageV3 frame per APK spec.

    Layout:
    [0-1]  dest device ID (UShort LE)
    [2-3]  src device ID (UShort LE)
    [4-5]  packet_size (UShort LE) = payload_len + 9
    [6]    control_type
    [7-15] segmentation header (9 bytes, all zero for non-segmented)
    [16]   MBP type (sub-protocol byte 0)
    [17]   correlation ID (sub-protocol byte 1)
    [18+]  CBOR payload
    """
    seg_header = bytes(9)  # no segmentation
    sub_header = bytes([mbp_type, correlation_id])
    payload = sub_header + cbor_payload
    packet_size = len(payload) + 9  # payload + segmentation header size

    header = struct.pack('<HH', dest, src)
    header += struct.pack('<H', packet_size)
    header += bytes([control_type])
    header += seg_header
    header += payload

    return header


def parse_v3_frame(data):
    """Parse a TruMessageV3 frame and return structured info."""
    if len(data) < 16:
        return None

    dest = struct.unpack_from('<H', data, 0)[0]
    src = struct.unpack_from('<H', data, 2)[0]
    pkt_size = struct.unpack_from('<H', data, 4)[0]
    control = data[6]
    seg_flags = data[7]

    ctrl_names = {
        0x01: 'REGISTRATION', 0x02: 'DISCOVERY', 0x03: 'MBP',
        0x04: 'FILE_MANAGER', 0x05: 'SECURITY', 0x06: 'FIRMWARE', 0x0A: 'NONE'
    }

    result = {
        'dest': f'0x{dest:04X}',
        'src': f'0x{src:04X}',
        'pkt_size': pkt_size,
        'control': ctrl_names.get(control, f'0x{control:02X}'),
        'control_raw': control,
        'seg_flags': f'0x{seg_flags:02X}',
    }

    if len(data) > 16:
        sub_type = data[16]
        corr_id = data[17] if len(data) > 17 else 0

        if control == CTRL_MBP:
            mbp_names = {
                0x00: 'INFO', 0x01: 'WRITE', 0x02: 'SUBSCRIBE',
                0x03: 'BINARY', 0x04: 'PARAM_DISC',
                0x82: 'SUBSCRIBE_RESP', 0x84: 'PARAM_DISC_RESP'
            }
            result['mbp_type'] = mbp_names.get(sub_type, f'0x{sub_type:02X}')
        elif control == CTRL_REGISTRATION:
            reg_names = {0x01: 'REQUEST', 0x02: 'RESPONSE', 0x03: 'DEREGISTER'}
            result['reg_type'] = reg_names.get(sub_type, f'0x{sub_type:02X}')

        result['sub_type'] = f'0x{sub_type:02X}'
        result['corr_id'] = corr_id

        # Try CBOR decode
        if len(data) > 18:
            try:
                cbor_data = cbor2.loads(data[18:])
                result['cbor'] = cbor_data
            except Exception:
                result['raw_payload'] = data[18:].hex()

    return result


class ProtocolTester:
    def __init__(self):
        self.bus = None
        self.chars = {}
        self._char_ifaces = {}
        self.seq = 0
        self.identity = load_identity()
        self._transport_event = None
        self._transport_ack = None
        self._notifications = []
        self._assigned_device_id = None
        self.device = None

    async def connect(self):
        self.bus = await MessageBus(bus_type=BusType.SYSTEM).connect()

        om_intr = await self.bus.introspect(BLUEZ, "/")
        om_obj = self.bus.get_proxy_object(BLUEZ, "/", om_intr)
        self._om = om_obj.get_interface("org.freedesktop.DBus.ObjectManager")

        # Scan for Truma
        print("Scanning for Truma...")
        intr = await self.bus.introspect(BLUEZ, "/org/bluez/hci1")
        obj = self.bus.get_proxy_object(BLUEZ, "/org/bluez/hci1", intr)
        adapter = obj.get_interface("org.bluez.Adapter1")
        await adapter.call_start_discovery()
        await asyncio.sleep(10)

        dev_path = await self._find_truma()
        await adapter.call_stop_discovery()
        await asyncio.sleep(1)

        if not dev_path:
            raise Exception("Truma not found!")

        introspect = await self.bus.introspect(BLUEZ, dev_path)
        dev_obj = self.bus.get_proxy_object(BLUEZ, dev_path, introspect)
        self.device = dev_obj.get_interface("org.bluez.Device1")
        self._dev_path = dev_path

        # Set trusted
        props = dev_obj.get_interface("org.freedesktop.DBus.Properties")
        await props.call_set("org.bluez.Device1", "Trusted", Variant("b", True))

        # Connect
        for attempt in range(3):
            try:
                print(f"Connecting (attempt {attempt+1})...")
                await self.device.call_connect()
                print("Connected!")
                break
            except Exception as e:
                if "Already Connected" in str(e):
                    print("Already connected")
                    break
                if attempt < 2:
                    print(f"  Retry ({e})")
                    await asyncio.sleep(3)
                else:
                    raise

        # Brief settle time after connect
        await asyncio.sleep(1)

        # Wait for services
        props = dev_obj.get_interface("org.freedesktop.DBus.Properties")
        for _ in range(30):
            resolved = await props.call_get("org.bluez.Device1", "ServicesResolved")
            if resolved.value:
                break
            await asyncio.sleep(0.5)

        await self._discover_chars(dev_path)
        await self._subscribe_notifications()

    async def _find_truma(self):
        objects = await self._om.call_get_managed_objects()
        for path, ifaces in objects.items():
            if "org.bluez.Device1" not in ifaces:
                continue
            dev = ifaces["org.bluez.Device1"]
            name = dev.get("Name")
            name = name.value if name else ""
            if "iNet" in str(name) or "ruma" in str(name):
                addr = dev.get("Address")
                addr = addr.value if addr else "?"
                print(f"Found: {name} ({addr})")
                return path
        return None

    async def _discover_chars(self, dev_path):
        objects = await self._om.call_get_managed_objects()
        for path, interfaces in objects.items():
            if not path.startswith(dev_path):
                continue
            if "org.bluez.GattCharacteristic1" in interfaces:
                props = interfaces["org.bluez.GattCharacteristic1"]
                uuid = str(props["UUID"].value).lower()
                self.chars[uuid] = path
                flags = [str(f.value) if hasattr(f, 'value') else str(f)
                         for f in props.get("Flags", {}).value] if "Flags" in props else []
                print(f"  CHAR {uuid[-4:]}: {', '.join(flags)}")

    async def _subscribe_notifications(self):
        for char_uuid in [CHAR_DATA_R, CHAR_CMD_ALT, CHAR_CMD]:
            if char_uuid not in self.chars:
                continue
            try:
                path = self.chars[char_uuid]
                introspect = await self.bus.introspect(BLUEZ, path)
                obj = self.bus.get_proxy_object(BLUEZ, path, introspect)
                iface = obj.get_interface("org.bluez.GattCharacteristic1")
                props = obj.get_interface("org.freedesktop.DBus.Properties")
                props.on_properties_changed(self._make_handler(char_uuid))
                await iface.call_start_notify()
                print(f"  Notify ON: {char_uuid[-4:]}")
            except Exception as e:
                print(f"  Notify FAIL {char_uuid[-4:]}: {e}")

    def _make_handler(self, char_uuid):
        char_label = {
            CHAR_CMD: 'CMD', CHAR_DATA_R: 'DATA', CHAR_CMD_ALT: 'CMD2'
        }.get(char_uuid, '????')

        def handler(iface, changed, invalidated):
            if "Value" in changed:
                data = bytes(changed["Value"].value)
                ts = time.strftime('%H:%M:%S')
                print(f"  [{ts}] RX {char_label} ({len(data)}b): {data.hex()}")

                # Log notification
                self._notifications.append({
                    'time': ts, 'char': char_label,
                    'len': len(data), 'hex': data.hex(), 'raw': data
                })

                # Transport ACK
                if len(data) <= 4:
                    self._transport_ack = data
                    if self._transport_event:
                        self._transport_event.set()
                    return

                if self._transport_event:
                    self._transport_event.set()

                # Parse V3 frame
                parsed = parse_v3_frame(data)
                if parsed:
                    print(f"         -> {parsed.get('control')} "
                          f"src={parsed.get('src')} dst={parsed.get('dest')}")
                    if 'mbp_type' in parsed:
                        print(f"         -> MBP: {parsed['mbp_type']}")
                    if 'cbor' in parsed:
                        cbor_str = json.dumps(parsed['cbor'], default=str)
                        if len(cbor_str) > 120:
                            cbor_str = cbor_str[:120] + '...'
                        print(f"         -> CBOR: {cbor_str}")

        return handler

    async def _get_char(self, uuid):
        if uuid not in self._char_ifaces:
            path = self.chars[uuid]
            intr = await self.bus.introspect(BLUEZ, path)
            obj = self.bus.get_proxy_object(BLUEZ, path, intr)
            self._char_ifaces[uuid] = obj.get_interface("org.bluez.GattCharacteristic1")
        return self._char_ifaces[uuid]

    async def _write(self, uuid, data):
        char = await self._get_char(uuid)
        await char.call_write_value(bytes(data), {"type": Variant("s", "command")})

    async def _send_transport(self, packet):
        """Send via transport FSM (InitDataTransfer handshake)."""
        try:
            # Step 1: InitDataTransfer(size) on CMD
            announce = bytes([0x01, len(packet) & 0xFF, (len(packet) >> 8) & 0xFF])
            self._transport_event = asyncio.Event()
            self._transport_ack = None
            ts = time.strftime('%H:%M:%S')
            print(f"  [{ts}] TX CMD (3b): {announce.hex()}  [InitDataTransfer size={len(packet)}]")
            await self._write(CHAR_CMD, announce)

            # Step 2: Wait for ReadyStatus response
            try:
                await asyncio.wait_for(self._transport_event.wait(), timeout=3.0)
            except asyncio.TimeoutError:
                print("  [WARN] Timeout waiting for ready ACK")
            self._transport_event.clear()

            # Step 3: Send data on DATA_WRITE
            ts = time.strftime('%H:%M:%S')
            print(f"  [{ts}] TX DATA ({len(packet)}b): {packet.hex()}")
            await self._write(CHAR_DATA_W, packet)

            # Step 4: Wait for AckDataTransfer
            try:
                await asyncio.wait_for(self._transport_event.wait(), timeout=3.0)
            except asyncio.TimeoutError:
                print("  [WARN] Timeout waiting for data ACK")
            self._transport_event.clear()

            # Step 5: Wait for announcement and send confirmation
            try:
                await asyncio.wait_for(self._transport_event.wait(), timeout=3.0)
                if self._transport_ack and len(self._transport_ack) > 0 and self._transport_ack[0] == 0x83:
                    ts = time.strftime('%H:%M:%S')
                    print(f"  [{ts}] TX CMD (2b): 0300  [confirm]")
                    await self._write(CHAR_CMD, b'\x03\x00')
            except asyncio.TimeoutError:
                pass

        except Exception as e:
            print(f"  [ERR] Transport: {e}")
        finally:
            self._transport_event = None
            self._transport_ack = None

    # === TEST STEPS ===

    async def test_registration(self):
        """Test: Send proper V3 registration request per APK spec."""
        print("\n" + "=" * 60)
        print("TEST 1: Registration (DEVICE_REGISTRATION 0x01)")
        print("=" * 60)

        cbor_payload = cbor2.dumps({'pv': [5, 1]})
        frame = build_v3_frame(
            dest=DEV_BROADCAST,  # or DEV_PANEL
            src=DEV_APP,
            control_type=CTRL_REGISTRATION,
            mbp_type=0x01,  # REQUEST
            correlation_id=0x42,
            cbor_payload=cbor_payload
        )
        print(f"  Frame: {frame.hex()}")
        parsed = parse_v3_frame(frame)
        print(f"  Parsed: dest={parsed['dest']} src={parsed['src']} "
              f"ctrl={parsed['control']} sub=0x{parsed['sub_type']}")

        await self._send_transport(frame)
        await asyncio.sleep(2)

        print(f"\n  Received {len(self._notifications)} notifications so far")

    async def test_subscribe(self):
        """Test: Subscribe to topics per APK spec."""
        print("\n" + "=" * 60)
        print("TEST 2: Topic Subscription (MBP SUBSCRIBE 0x02)")
        print("=" * 60)

        batches = [
            ['AirCirculation', 'AirCooling', 'AirHeating', 'DeviceManagement',
             'EnergySrc', 'ErrorReset', 'FreshWater', 'GasBtl', 'GasControl', 'GreyWater'],
            ['Identify', 'L1Bat', 'L2Bat', 'LinePower', 'MobileIdentity',
             'PowerSupply', 'RoomClimate', 'Switches', 'Temperature', 'Transfer'],
            ['VBat', 'WaterHeating', 'AmbientLight', 'Panel', 'BatteryMngmt',
             'Install', 'Connect', 'TimerConfig', 'BleDeviceManagement', 'BluetoothDevice'],
            ['System', 'Resources', 'PowerMgmt']
        ]

        for i, batch in enumerate(batches):
            cbor_payload = cbor2.dumps({'tn': batch})
            frame = build_v3_frame(
                dest=DEV_MSG_BROKER,
                src=DEV_APP,
                control_type=CTRL_MBP,
                mbp_type=MBP_SUBSCRIBE,
                correlation_id=0,
                cbor_payload=cbor_payload
            )
            print(f"  Batch {i+1}: {len(batch)} topics")
            await self._send_transport(frame)
            await asyncio.sleep(0.25)  # APK uses 250ms

        await asyncio.sleep(2)
        print(f"\n  Total notifications: {len(self._notifications)}")

    async def test_identity(self):
        """Test: Send MobileIdentity + SystemTime per APK spec."""
        print("\n" + "=" * 60)
        print("TEST 3: Identity + SystemTime (MBP WRITE 0x01)")
        print("=" * 60)

        # SystemTime
        for param, value in [('Time', int(time.time())), ('Lot', 0)]:
            cbor_payload = cbor2.dumps({'tn': 'SystemTime', 'pn': param, 'v': value})
            frame = build_v3_frame(
                dest=DEV_PANEL, src=DEV_APP,
                control_type=CTRL_MBP, mbp_type=MBP_WRITE,
                correlation_id=0, cbor_payload=cbor_payload
            )
            await self._send_transport(frame)
            await asyncio.sleep(0.1)
        print("  Sent SystemTime")

        # MobileIdentity
        for param, value in [
            ('UserName', self.identity['username']),
            ('Muid', self.identity['muid']),
            ('Uuid', self.identity['uuid']),
        ]:
            cbor_payload = cbor2.dumps({'tn': 'MobileIdentity', 'pn': param, 'v': value})
            frame = build_v3_frame(
                dest=DEV_PANEL, src=DEV_APP,
                control_type=CTRL_MBP, mbp_type=MBP_WRITE,
                correlation_id=0, cbor_payload=cbor_payload
            )
            await self._send_transport(frame)
            await asyncio.sleep(0.1)
        print("  Sent MobileIdentity")

        await asyncio.sleep(2)

    async def test_listen(self, duration=15):
        """Listen for INFO_MESSAGE updates and decode them."""
        print("\n" + "=" * 60)
        print(f"TEST 4: Listen for updates ({duration}s)")
        print("=" * 60)

        start = len(self._notifications)
        await asyncio.sleep(duration)
        new = self._notifications[start:]
        print(f"\n  Received {len(new)} new notifications")

        # Parse and summarize
        params = {}
        for n in new:
            if n['len'] > 16:
                parsed = parse_v3_frame(n['raw'])
                if parsed and 'cbor' in parsed:
                    cbor = parsed['cbor']
                    if isinstance(cbor, dict) and 'tn' in cbor and 'pn' in cbor:
                        key = f"{cbor['tn']}.{cbor['pn']}"
                        params[key] = cbor.get('v')

        if params:
            print("\n  Decoded parameters:")
            for k, v in sorted(params.items()):
                if isinstance(v, (int, float)):
                    # Check if it's a temperature (ends with Temp or TgtTemp)
                    if 'Temp' in k and isinstance(v, int) and v > 100:
                        print(f"    {k}: {v} ({v/10:.1f}°C)")
                    else:
                        print(f"    {k}: {v}")
                else:
                    print(f"    {k}: {v}")

    async def disconnect(self):
        if self.device:
            try:
                await self.device.call_disconnect()
            except Exception:
                pass
        print("\nDisconnected")

    async def run_all_tests(self):
        """Run all protocol tests in sequence."""
        await self.connect()
        print("\n" + "#" * 60)
        print("  TRUMA PROTOCOL VERIFICATION TEST")
        print("  Testing APK findings against real device")
        print("#" * 60)

        await self.test_registration()
        await self.test_subscribe()
        await self.test_identity()
        await self.test_listen(duration=10)

        # Summary
        print("\n" + "#" * 60)
        print("  SUMMARY")
        print("#" * 60)
        print(f"  Total notifications received: {len(self._notifications)}")

        cmd_notifs = [n for n in self._notifications if n['char'] == 'CMD']
        data_notifs = [n for n in self._notifications if n['char'] == 'DATA']
        print(f"  CMD notifications: {len(cmd_notifs)}")
        print(f"  DATA notifications: {len(data_notifs)}")

        # Check for V3 frame signatures
        v3_frames = 0
        for n in data_notifs:
            if n['len'] >= 16:
                parsed = parse_v3_frame(n['raw'])
                if parsed:
                    v3_frames += 1
        print(f"  Valid V3 frames: {v3_frames}")

        await self.disconnect()


async def main():
    tester = ProtocolTester()
    try:
        await tester.run_all_tests()
    except KeyboardInterrupt:
        print("\nInterrupted")
        await tester.disconnect()
    except Exception as e:
        print(f"\nError: {e}")
        import traceback
        traceback.print_exc()
        await tester.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
