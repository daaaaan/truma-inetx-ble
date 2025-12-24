#!/usr/bin/env python3
"""Control Truma iNetX via BLE.

Send commands to control heating, temperature, water heating, etc.
"""

import asyncio
import struct
import cbor2
from bleak import BleakClient, BleakScanner

# Truma BLE UUIDs
SERVICE_UUID = "fc314000-f3b2-11e8-8eb2-f2801f1b9fd1"
CHAR_WRITE = "fc314001-f3b2-11e8-8eb2-f2801f1b9fd1"      # Write with response
CHAR_WRITE_NR = "fc314002-f3b2-11e8-8eb2-f2801f1b9fd1"  # Write no response
CHAR_NOTIFY_1 = "fc314003-f3b2-11e8-8eb2-f2801f1b9fd1"  # Notify
CHAR_NOTIFY_2 = "fc314004-f3b2-11e8-8eb2-f2801f1b9fd1"  # Notify


class TrumaController:
    """Controller for Truma iNetX."""

    def __init__(self):
        self.client = None
        self.seq = 0
        self._transport_event = None
        self._last_notify = None

    def _build_command(self, topic: str, param: str, value) -> bytes:
        """Build a command packet matching the captured protocol format.

        Args:
            topic: Topic name (e.g., 'RoomClimate', 'AirHeating')
            param: Parameter name (e.g., 'Mode', 'TgtTemp')
            value: Value to set
        """
        self.seq += 1

        # CBOR payload - map with 4 keys (matching captured format exactly)
        payload = {
            'pn': param,
            'tn': topic,
            'v': value,
            'id': 0,
        }
        cbor_data = cbor2.dumps(payload)

        # Header (18 bytes) - format from captured traffic analysis
        header = bytearray(18)
        header[0] = 0x01                    # Constant
        header[1] = self.seq & 0xFF         # Sequence number
        header[2] = 0x00                    # Marker byte 1
        header[3] = 0x05                    # Marker byte 2
        # Length field = CBOR length + 11 (accounts for header bytes 6-16)
        total_len = len(cbor_data) + 11
        struct.pack_into('<H', header, 4, total_len)
        header[6] = 0x03                    # Message type byte 1
        header[7] = 0x00                    # Message type byte 2
        # Bytes 8-15 are zeros (reserved/padding)
        header[16] = 0x01                   # Command type byte 1
        header[17] = 0x00                   # Command type byte 2

        return bytes(header) + cbor_data

    async def connect(self, address: str = None):
        """Connect to iNetX."""
        if address is None:
            print("Scanning for iNetX...")
            devices = await BleakScanner.discover(timeout=5.0)
            inetx = next((d for d in devices if d.name and 'inetx' in d.name.lower()), None)
            if not inetx:
                raise Exception("iNetX not found!")
            address = inetx.address
            print(f"Found: {inetx.name} ({address})")

        print("Connecting...")
        self.client = BleakClient(address, timeout=30.0)
        await self.client.connect()
        print("Connected!")

        # Discover services
        services = self.client.services
        print(f"Services discovered")

        # Subscribe to notifications on all relevant characteristics
        try:
            # Transport layer notifications come on CHAR_WRITE (0x22)
            try:
                await self.client.start_notify(CHAR_WRITE, self._on_notify)
            except Exception:
                pass  # May not support notifications
            await self.client.start_notify(CHAR_NOTIFY_1, self._on_notify)
            await self.client.start_notify(CHAR_NOTIFY_2, self._on_notify)
            print("Subscribed to notifications")
        except Exception as e:
            print(f"Notifications not available (pairing may be needed): {e}")

        # Run initialization handshake
        await self._init_handshake()

    async def _init_handshake(self):
        """Send initialization sequence required by iNetX (from captured traffic)."""
        import time
        import uuid

        print("Sending init handshake...")

        # 1. Send protocol version (special header format)
        await self._send_protocol_version()
        await asyncio.sleep(0.1)

        # 2. Subscribe to topics (required before commands work)
        await self._subscribe_topics()
        await asyncio.sleep(0.1)

        # 3. Send SystemTime
        system_time = int(time.time())
        await self._send_raw_cbor({
            'avail': 1,
            'topics': [{
                'tn': 'SystemTime',
                'id': 0,
                'parameters': [
                    {'v': system_time, 'id': 0, 'type': 18, 'pn': 'Time', 'tn': 'SystemTime'},
                    {'v': 0, 'id': 0, 'type': 1, 'pn': 'Lot', 'tn': 'SystemTime'}
                ]
            }]
        })
        await asyncio.sleep(0.1)

        # 4. Send MobileIdentity
        device_uuid = str(uuid.uuid4())
        await self._send_raw_cbor({
            'avail': 1,
            'topics': [{
                'tn': 'MobileIdentity',
                'id': 0,
                'parameters': [
                    {'v': 'Vanlin Controller', 'id': 0, 'type': 4, 'pn': 'UserName', 'tn': 'MobileIdentity'}
                ]
            }]
        })
        await asyncio.sleep(0.1)

        await self._send_raw_cbor({
            'avail': 1,
            'topics': [{
                'tn': 'MobileIdentity',
                'id': 0,
                'parameters': [
                    {'v': device_uuid, 'id': 0, 'type': 4, 'pn': 'Muid', 'tn': 'MobileIdentity'},
                    {'v': device_uuid, 'id': 0, 'type': 4, 'pn': 'Uuid', 'tn': 'MobileIdentity'}
                ]
            }]
        })
        await asyncio.sleep(0.1)

        # 5. Send LastMessage marker
        await self._send_raw_cbor({'LastMessage': 1})
        await asyncio.sleep(0.3)

        print("Init handshake complete")

    async def _send_protocol_version(self):
        """Send protocol version packet (special format from capture)."""
        # Captured: 0000ffff120001000000000000000000019ea1627076820501
        # This has a different header format than regular messages
        cbor_data = cbor2.dumps({'pv': [5, 1]})

        header = bytearray(18)
        header[0] = 0x00
        header[1] = 0x00
        header[2] = 0xff
        header[3] = 0xff
        struct.pack_into('<H', header, 4, len(cbor_data) + 11)
        header[6] = 0x00
        header[7] = 0x01
        # Bytes 8-15 zeros
        header[16] = 0x01
        header[17] = 0x9e

        packet = bytes(header) + cbor_data
        await self._send_with_transport(packet)

    async def _subscribe_topics(self):
        """Subscribe to required topics before commands can work."""
        # Topics discovered from capture - split into batches like the app does
        topics_batch1 = [
            'AirCirculation', 'AirCooling', 'AirHeating', 'DeviceManagement',
            'EnergySrc', 'ErrorReset', 'FreshWater', 'GasBtl', 'GasControl', 'GreyWater'
        ]
        topics_batch2 = [
            'Identify', 'L1Bat', 'L2Bat', 'LinePower', 'MobileIdentity',
            'PowerSupply', 'RoomClimate', 'Switches', 'Temperature', 'Transfer'
        ]
        topics_batch3 = [
            'VBat', 'WaterHeating', 'AmbientLight', 'Panel', 'BatteryMngmt',
            'Install', 'Connect', 'TimerConfig', 'BleDeviceManagement', 'BluetoothDevice'
        ]
        topics_batch4 = ['System', 'Resources', 'PowerMgmt']

        for batch in [topics_batch1, topics_batch2, topics_batch3, topics_batch4]:
            await self._send_topic_subscription(batch)
            await asyncio.sleep(0.05)

    async def _send_topic_subscription(self, topics: list):
        """Send a topic subscription message."""
        self.seq += 1
        cbor_data = cbor2.dumps({'tn': topics})

        header = bytearray(18)
        header[0] = 0x00
        header[1] = 0x00
        header[2] = 0x00
        header[3] = 0x05
        struct.pack_into('<H', header, 4, len(cbor_data) + 11)
        header[6] = 0x03
        header[7] = 0x00
        header[16] = 0x02  # cmd_type 0x0002 for subscriptions
        header[17] = 0x00

        packet = bytes(header) + cbor_data
        await self._send_with_transport(packet)

    async def _send_with_transport(self, packet: bytes):
        """Send a packet, attempting transport protocol if available.

        Transport protocol (from captured traffic):
        1. Write 01 <len_lo> <len_hi> to CHAR_WRITE (0x22) to announce message size
        2. Wait for 8100 acknowledgment notification
        3. Write actual message to CHAR_WRITE_NR (0x25)
        4. Wait for f001 flow control notification
        5. Send 0300 ACK if we receive data (83xx00)
        """
        # Try transport protocol, fall back to direct send if it fails
        try:
            # Announce message length
            length_announce = bytes([0x01, len(packet) & 0xFF, (len(packet) >> 8) & 0xFF])
            self._transport_event = asyncio.Event()
            await self.client.write_gatt_char(CHAR_WRITE, length_announce, response=True)

            # Wait for 8100 acknowledgment
            try:
                await asyncio.wait_for(self._transport_event.wait(), timeout=1.0)
            except asyncio.TimeoutError:
                pass

            self._transport_event.clear()
        except Exception as e:
            # Transport layer not available, skip to direct send
            pass

        # Send actual message
        await self.client.write_gatt_char(CHAR_WRITE_NR, packet, response=False)

        # Brief wait for response
        await asyncio.sleep(0.1)

        self._transport_event = None

    async def _send_raw_cbor(self, data):
        """Send raw CBOR data with header."""
        self.seq += 1
        cbor_data = cbor2.dumps(data)

        header = bytearray(18)
        header[0] = 0x01
        header[1] = self.seq & 0xFF
        header[2:4] = b'\x00\x05'
        # Length field = CBOR length + 11 (consistent with captured format)
        struct.pack_into('<H', header, 4, len(cbor_data) + 11)
        header[6:8] = b'\x03\x00'
        header[16:18] = b'\x01\x00'

        packet = bytes(header) + cbor_data
        await self._send_with_transport(packet)

    def _on_notify(self, sender, data: bytes):
        """Handle notifications from device."""
        self._last_notify = data
        if self._transport_event:
            self._transport_event.set()
        # Only print if not a simple ACK
        if len(data) > 3:
            print(f"[NOTIFY] {data.hex()[:60]}...")

    async def disconnect(self):
        """Disconnect from device."""
        if self.client:
            await self.client.disconnect()

    async def send_command(self, topic: str, param: str, value):
        """Send a control command using transport protocol."""
        cmd = self._build_command(topic, param, value)
        print(f"Sending: {topic}.{param} = {value}")
        await self._send_with_transport(cmd)
        await asyncio.sleep(0.2)  # Brief pause between commands

    # High-level commands

    async def set_heating_mode(self, mode: str):
        """Set room climate mode.

        Args:
            mode: 'off', 'heating', or 'ventilating'
        """
        modes = {'off': 0, 'heating': 3, 'ventilating': 5}
        value = modes.get(mode.lower(), 0)
        await self.send_command('RoomClimate', 'Mode', value)

    async def set_target_temp(self, temp_c: float):
        """Set target temperature in Celsius (for AirHeating).

        Args:
            temp_c: Temperature in Celsius (e.g., 20.0 for 20°C)
                    Range: 5.0 to 30.0
        """
        value = int(temp_c * 10)  # Convert to decicelsius
        await self.send_command('AirHeating', 'TgtTemp', value)

    async def set_water_heating_mode(self, mode: str):
        """Set water heating mode.

        Args:
            mode: 'off', '40', '60', 'eco', or 'comfort'
        """
        modes = {'off': 0, '40': 1, '60': 2, 'eco': 1, 'comfort': 2}
        value = modes.get(mode.lower(), 0)
        await self.send_command('WaterHeating', 'Mode', value)


async def main():
    """Example usage."""
    ctrl = TrumaController()

    try:
        await ctrl.connect()

        print("\n--- Setting heating to ON, 20°C ---")
        await ctrl.set_heating_mode('heating')
        await ctrl.set_target_temp(20.0)

        print("\nCommands sent! Waiting for responses...")
        await asyncio.sleep(3)

    finally:
        await ctrl.disconnect()
        print("Disconnected.")


if __name__ == "__main__":
    asyncio.run(main())
