#!/usr/bin/env python3
"""Truma iNetX BLE controller using dbus-fast (no bleak dependency).

Designed to run on Venus OS / Cerbo GX where bleak is not available.
Uses BlueZ D-Bus API directly via dbus-fast.
"""

import asyncio
import struct
import cbor2
import json
import os
import time
import uuid
from pathlib import Path
from dbus_fast.aio import MessageBus
from dbus_fast import BusType, Variant

TRUMA_ADDRESS = "74:AD:C6:91:04:98"
IDENTITY_FILE = Path(__file__).parent / ".truma_identity.json"

# Truma BLE UUIDs
SERVICE_UUID = "fc314000-f3b2-11e8-8eb2-f2801f1b9fd1"
CHAR_WRITE = "fc314001-f3b2-11e8-8eb2-f2801f1b9fd1"
CHAR_WRITE_NR = "fc314002-f3b2-11e8-8eb2-f2801f1b9fd1"
CHAR_NOTIFY_1 = "fc314003-f3b2-11e8-8eb2-f2801f1b9fd1"
CHAR_NOTIFY_2 = "fc314004-f3b2-11e8-8eb2-f2801f1b9fd1"

BLUEZ = "org.bluez"


def addr_to_path(addr):
    return f"/org/bluez/hci0/dev_{addr.replace(':', '_')}"


def load_identity():
    if IDENTITY_FILE.exists():
        try:
            with open(IDENTITY_FILE) as f:
                identity = json.load(f)
                print(f"Loaded identity: {identity['muid'][:8]}...")
                return identity
        except Exception:
            pass
    identity = {
        "muid": str(uuid.uuid4()).upper(),
        "uuid": str(uuid.uuid4()).lower(),
        "username": "Vanlin Controller"
    }
    with open(IDENTITY_FILE, 'w') as f:
        json.dump(identity, f, indent=2)
    print(f"Created new identity: {identity['muid'][:8]}...")
    return identity


class TrumaDbus:
    def __init__(self):
        self.bus = None
        self.device = None
        self.chars = {}  # uuid -> dbus path
        self._char_ifaces = {}  # uuid -> cached interface
        self.seq = 0
        self.identity = load_identity()
        self._transport_event = None
        self._transport_ack = None
        self.status = {}

    async def connect(self, address=None):
        self.bus = await MessageBus(bus_type=BusType.SYSTEM).connect()

        om_intr = await self.bus.introspect(BLUEZ, "/")
        om_obj = self.bus.get_proxy_object(BLUEZ, "/", om_intr)
        self._om = om_obj.get_interface("org.freedesktop.DBus.ObjectManager")

        # First check for already-paired Truma (no scan needed)
        dev_path = await self._find_truma(paired_only=True)

        # Fallback: scan for Truma
        if not dev_path:
            print("No paired Truma found, scanning...")
            adapter_intr = await self.bus.introspect(BLUEZ, "/org/bluez/hci0")
            adapter_obj = self.bus.get_proxy_object(BLUEZ, "/org/bluez/hci0", adapter_intr)
            adapter = adapter_obj.get_interface("org.bluez.Adapter1")
            try:
                await adapter.call_stop_discovery()
            except Exception:
                pass
            await asyncio.sleep(1)
            await adapter.call_set_discovery_filter({"Transport": Variant("s", "le")})
            await adapter.call_start_discovery()
            await asyncio.sleep(8)
            await adapter.call_stop_discovery()
            await asyncio.sleep(1)
            dev_path = await self._find_truma(paired_only=False)

        if not dev_path:
            raise Exception("Truma iNetX not found!")

        # Get device proxy and connect
        introspect = await self.bus.introspect(BLUEZ, dev_path)
        dev_obj = self.bus.get_proxy_object(BLUEZ, dev_path, introspect)
        self.device = dev_obj.get_interface("org.bluez.Device1")
        self._dev_path = dev_path

        # Connect with retries
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
                    print(f"  Retry ({e})...")
                    await asyncio.sleep(3)
                else:
                    raise

        # Wait for services to resolve
        print("Waiting for services...")
        props = dev_obj.get_interface("org.freedesktop.DBus.Properties")
        for _ in range(30):
            resolved = await props.call_get(BLUEZ + ".Device1", "ServicesResolved")
            if resolved.value:
                break
            await asyncio.sleep(0.5)
        else:
            print("WARNING: Services not resolved after 15s")
            return

        # Discover characteristics
        await self._discover_chars(self._dev_path)
        print(f"Found {len(self.chars)} characteristics")

        # Subscribe to notifications
        await self._subscribe_notifications()

        # Run init handshake
        await self._init_handshake()

    async def _find_truma(self, paired_only=False):
        """Find Truma device in BlueZ object tree."""
        objects = await self._om.call_get_managed_objects()
        for path, ifaces in objects.items():
            if "org.bluez.Device1" not in ifaces:
                continue
            dev = ifaces["org.bluez.Device1"]
            name = dev.get("Name")
            name = name.value if name else ""
            paired = dev.get("Paired")
            paired = paired.value if paired else False
            if paired_only and not paired:
                continue
            if "iNet" in str(name) or "ruma" in str(name):
                addr = dev.get("Address")
                addr = addr.value if addr else "?"
                print(f"Found: {name} ({addr}) paired={paired}")
                return path
        return None

    async def _discover_chars(self, dev_path):
        """Find GATT characteristics via D-Bus object manager."""
        objects = await self._om.call_get_managed_objects()

        for path, interfaces in objects.items():
            if not path.startswith(dev_path):
                continue
            if "org.bluez.GattCharacteristic1" in interfaces:
                char_props = interfaces["org.bluez.GattCharacteristic1"]
                char_uuid = str(char_props["UUID"].value).lower()
                self.chars[char_uuid] = path

        for name, uuid_val in [("WRITE", CHAR_WRITE), ("WRITE_NR", CHAR_WRITE_NR),
                                ("NOTIFY_1", CHAR_NOTIFY_1), ("NOTIFY_2", CHAR_NOTIFY_2)]:
            found = "OK" if uuid_val in self.chars else "MISSING"
            print(f"  {name}: {found}")

    async def _subscribe_notifications(self):
        """Enable notifications on notify characteristics."""
        for char_uuid in [CHAR_NOTIFY_1, CHAR_NOTIFY_2, CHAR_WRITE]:
            if char_uuid not in self.chars:
                continue
            try:
                path = self.chars[char_uuid]
                introspect = await self.bus.introspect(BLUEZ, path)
                obj = self.bus.get_proxy_object(BLUEZ, path, introspect)
                char_iface = obj.get_interface("org.bluez.GattCharacteristic1")
                # Subscribe to property changes via dbus-fast signal
                props_iface = obj.get_interface("org.freedesktop.DBus.Properties")
                props_iface.on_properties_changed(self._make_prop_handler(char_uuid))
                await char_iface.call_start_notify()
                print(f"  Subscribed: {char_uuid[-4:]}")
            except Exception as e:
                print(f"  Notify failed {char_uuid[-4:]}: {e}")

    def _make_prop_handler(self, char_uuid):
        """Create a properties changed handler for a characteristic."""
        def handler(iface, changed, invalidated):
            if "Value" in changed:
                data = bytes(changed["Value"].value)
                self._handle_notification(data)
        return handler

    def _on_dbus_message(self, msg):
        """Handle D-Bus signals for GATT notifications."""
        if msg.member != "PropertiesChanged":
            return False
        if msg.signature != "sa{sv}as":
            return False

        args = msg.body
        if len(args) < 2:
            return False
        iface = args[0]
        changed = args[1]

        if iface == "org.bluez.GattCharacteristic1" and "Value" in changed:
            data = bytes(changed["Value"].value)
            self._handle_notification(data)

        return False

    def _handle_notification(self, data):
        """Process notification data from Truma."""
        print(f"  [N] {len(data)}b: {data[:8].hex()}...", flush=True)

        # Transport ACKs (short messages)
        if len(data) <= 4:
            self._transport_ack = data
            if self._transport_event:
                self._transport_event.set()
            return

        # Signal transport event
        if self._transport_event:
            self._transport_event.set()

        # Decode CBOR
        cbor_data = self._decode_notification(data)
        if cbor_data:
            self._update_status(cbor_data)

    def _decode_notification(self, data):
        if len(data) < 20:
            return None
        for offset in [18, 16, 20, 8]:
            if offset >= len(data):
                continue
            try:
                decoded = cbor2.loads(data[offset:])
                if isinstance(decoded, dict):
                    return decoded
            except Exception:
                continue
        for i in range(len(data)):
            if data[i] in (0xbf, 0xa1, 0xa2, 0xa3, 0xa4, 0xa5, 0xa6):
                try:
                    decoded = cbor2.loads(data[i:])
                    if isinstance(decoded, dict):
                        return decoded
                except Exception:
                    continue
        return None

    def _update_status(self, cbor_data):
        """Update status from CBOR notification."""
        if 'topics' in cbor_data:
            for topic in cbor_data['topics']:
                if not isinstance(topic, dict):
                    continue
                tn = topic.get('tn', '')
                for param in topic.get('parameters', []):
                    if isinstance(param, dict):
                        pn = param.get('pn', '')
                        v = param.get('v')
                        if pn and v is not None:
                            self.status[f"{tn}.{pn}"] = v

    async def _get_char_iface(self, char_uuid):
        """Get cached GATT characteristic interface."""
        if char_uuid not in self._char_ifaces:
            path = self.chars[char_uuid]
            introspect = await self.bus.introspect(BLUEZ, path)
            obj = self.bus.get_proxy_object(BLUEZ, path, introspect)
            self._char_ifaces[char_uuid] = obj.get_interface("org.bluez.GattCharacteristic1")
        return self._char_ifaces[char_uuid]

    async def _write_char(self, char_uuid, data, response=True):
        """Write to a GATT characteristic."""
        if char_uuid not in self.chars:
            raise Exception(f"Characteristic {char_uuid} not found")

        char = await self._get_char_iface(char_uuid)

        # Truma requires write-without-response (command) on all characteristics
        options = {"type": Variant("s", "command")}

        await char.call_write_value(bytes(data), options)

    async def _send_with_transport(self, packet):
        """Send using transport protocol."""
        try:
            # 1. Announce length
            length_announce = bytes([0x01, len(packet) & 0xFF, (len(packet) >> 8) & 0xFF])
            self._transport_event = asyncio.Event()
            self._transport_ack = None
            await self._write_char(CHAR_WRITE, length_announce, response=True)

            # 2. Wait for 0x81 ready ACK
            try:
                await asyncio.wait_for(self._transport_event.wait(), timeout=2.0)
            except asyncio.TimeoutError:
                pass
            self._transport_event.clear()

            # 3. Send actual message
            await self._write_char(CHAR_WRITE_NR, packet, response=False)

            # 4. Wait for 0xf0 flow control
            try:
                await asyncio.wait_for(self._transport_event.wait(), timeout=2.0)
            except asyncio.TimeoutError:
                pass
            self._transport_event.clear()

            # 5. Wait for 0x83 ACK and send confirmation
            try:
                await asyncio.wait_for(self._transport_event.wait(), timeout=2.0)
                if self._transport_ack and self._transport_ack[0:1] == b'\x83':
                    await self._write_char(CHAR_WRITE, b'\x03\x00', response=True)
            except asyncio.TimeoutError:
                pass
        except Exception as e:
            print(f"[TRANSPORT] Error: {e}")
        finally:
            self._transport_event = None
            self._transport_ack = None

    async def _init_handshake(self):
        """Run the init handshake sequence."""
        print("\nInit handshake...")

        # 1. Protocol version
        cbor_data = cbor2.dumps({'pv': [5, 1]})
        header = bytearray(18)
        header[0:4] = b'\x00\x00\xff\xff'
        struct.pack_into('<H', header, 4, len(cbor_data) + 11)
        header[6:8] = b'\x00\x01'
        header[16:18] = b'\x01\x9e'
        await self._send_with_transport(bytes(header) + cbor_data)
        await asyncio.sleep(0.1)
        print("  Sent protocol version")

        # 2. Topic subscriptions
        all_topics = [
            ['AirCirculation', 'AirCooling', 'AirHeating', 'DeviceManagement',
             'EnergySrc', 'ErrorReset', 'FreshWater', 'GasBtl', 'GasControl', 'GreyWater'],
            ['Identify', 'L1Bat', 'L2Bat', 'LinePower', 'MobileIdentity',
             'PowerSupply', 'RoomClimate', 'Switches', 'Temperature', 'Transfer'],
            ['VBat', 'WaterHeating', 'AmbientLight', 'Panel', 'BatteryMngmt',
             'Install', 'Connect', 'TimerConfig', 'BleDeviceManagement', 'BluetoothDevice'],
            ['System', 'Resources', 'PowerMgmt']
        ]
        for batch in all_topics:
            self.seq += 1
            cbor_data = cbor2.dumps({'tn': batch})
            header = bytearray(18)
            header[0:4] = b'\x00\x00\x00\x05'
            struct.pack_into('<H', header, 4, len(cbor_data) + 11)
            header[6:8] = b'\x03\x00'
            header[16:18] = b'\x02\x00'
            await self._send_with_transport(bytes(header) + cbor_data)
            await asyncio.sleep(0.05)
        print("  Subscribed to topics")

        # 3. SystemTime
        self.seq += 1
        await self._send_raw_cbor({
            'avail': 1,
            'topics': [{'tn': 'SystemTime', 'id': 0, 'parameters': [
                {'v': int(time.time()), 'id': 0, 'type': 18, 'pn': 'Time', 'tn': 'SystemTime'},
                {'v': 0, 'id': 0, 'type': 1, 'pn': 'Lot', 'tn': 'SystemTime'}
            ]}]
        })
        await asyncio.sleep(0.1)
        print("  Sent system time")

        # 4. MobileIdentity
        await self._send_raw_cbor({
            'avail': 1,
            'topics': [{'tn': 'MobileIdentity', 'id': 0, 'parameters': [
                {'v': self.identity['username'], 'id': 0, 'type': 4, 'pn': 'UserName', 'tn': 'MobileIdentity'}
            ]}]
        })
        await asyncio.sleep(0.1)

        await self._send_raw_cbor({
            'avail': 1,
            'topics': [{'tn': 'MobileIdentity', 'id': 0, 'parameters': [
                {'v': self.identity['muid'], 'id': 0, 'type': 4, 'pn': 'Muid', 'tn': 'MobileIdentity'},
                {'v': self.identity['uuid'], 'id': 0, 'type': 4, 'pn': 'Uuid', 'tn': 'MobileIdentity'}
            ]}]
        })
        await asyncio.sleep(0.1)
        print("  Sent identity")

        # 5. LastMessage
        await self._send_raw_cbor({'LastMessage': 1})
        await asyncio.sleep(0.3)
        print("  Init complete!")

    async def _send_raw_cbor(self, data):
        self.seq += 1
        cbor_data = cbor2.dumps(data)
        header = bytearray(18)
        header[0] = 0x01
        header[1] = self.seq & 0xFF
        header[2:4] = b'\x00\x05'
        struct.pack_into('<H', header, 4, len(cbor_data) + 11)
        header[6:8] = b'\x03\x00'
        header[16:18] = b'\x01\x00'
        await self._send_with_transport(bytes(header) + cbor_data)

    async def disconnect(self):
        if self.device:
            try:
                await self.device.call_disconnect()
            except Exception:
                pass
        print("Disconnected")

    def display_status(self):
        print("\n" + "=" * 50)
        print("        TRUMA HEATER STATUS")
        print("=" * 50)

        temp = self.status.get('Temperature.CurTemp')
        tgt = self.status.get('AirHeating.TgtTemp')
        mode = self.status.get('RoomClimate.Mode')
        water_mode = self.status.get('WaterHeating.Mode')
        water_temp = self.status.get('WaterHeating.CurTemp')
        voltage = self.status.get('PowerSupply.Volt')

        modes = {0: 'OFF', 3: 'HEATING', 5: 'VENTILATING'}
        water_modes = {0: 'OFF', 1: 'ECO (40C)', 2: 'HOT (60C)'}

        print(f"\n[ROOM CLIMATE]")
        print(f"  Mode:    {modes.get(mode, f'?({mode})')}")
        print(f"  Current: {temp/10:.1f}C" if temp is not None else "  Current: --")
        print(f"  Target:  {tgt/10:.1f}C" if tgt is not None else "  Target:  --")

        print(f"\n[WATER]")
        print(f"  Mode:    {water_modes.get(water_mode, f'?({water_mode})')}")
        print(f"  Temp:    {water_temp/10:.1f}C" if water_temp is not None else "  Temp:    --")

        print(f"\n[POWER]")
        print(f"  Voltage: {voltage/1000:.1f}V" if voltage is not None else "  Voltage: --")

        if self.status:
            print(f"\n[ALL PARAMETERS] ({len(self.status)} total)")
            for k, v in sorted(self.status.items()):
                print(f"  {k}: {v}")
        print("=" * 50)


async def main():
    ctrl = TrumaDbus()
    try:
        await ctrl.connect()
        print("\nWaiting for status updates (10s)...")
        await asyncio.sleep(10)
        ctrl.display_status()
    except KeyboardInterrupt:
        print("\nInterrupted")
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        await ctrl.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
