#!/usr/bin/env python3
"""Control Truma iNetX via BLE.

Send commands to control heating, temperature, water heating, etc.
"""

import asyncio
import struct
import cbor2
import json
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional, Any
from bleak import BleakClient, BleakScanner

# Identity file to persist client credentials across sessions
IDENTITY_FILE = Path(__file__).parent / ".truma_identity.json"

# Truma BLE UUIDs
SERVICE_UUID = "fc314000-f3b2-11e8-8eb2-f2801f1b9fd1"
CHAR_WRITE = "fc314001-f3b2-11e8-8eb2-f2801f1b9fd1"      # Write with response
CHAR_WRITE_NR = "fc314002-f3b2-11e8-8eb2-f2801f1b9fd1"  # Write no response
CHAR_NOTIFY_1 = "fc314003-f3b2-11e8-8eb2-f2801f1b9fd1"  # Notify
CHAR_NOTIFY_2 = "fc314004-f3b2-11e8-8eb2-f2801f1b9fd1"  # Notify


@dataclass
class TrumaStatus:
    """Current status of the Truma heater."""

    # Temperatures (in Celsius)
    current_room_temp: Optional[float] = None
    target_room_temp: Optional[float] = None
    current_water_temp: Optional[float] = None
    target_water_temp: Optional[float] = None

    # Operating modes
    room_climate_mode: Optional[int] = None  # 0=off, 3=heating, 5=vent
    water_heating_mode: Optional[int] = None  # 0=off, 1=eco/40, 2=hot/60
    energy_mode: Optional[int] = None  # Energy source selection

    # Status flags
    heating_active: bool = False
    water_heating_active: bool = False
    error_code: Optional[int] = None

    # Power/Energy
    voltage: Optional[float] = None
    electric_power: Optional[int] = None  # Watts

    # Raw data storage for debugging
    raw_params: dict = field(default_factory=dict)

    # Last update time
    last_update: Optional[datetime] = None

    def update_from_cbor(self, cbor_data: dict):
        """Update status from decoded CBOR notification data."""
        self.last_update = datetime.now()

        # Handle topic-based updates
        if 'topics' in cbor_data:
            for topic in cbor_data['topics']:
                if not isinstance(topic, dict):
                    continue
                topic_name = topic.get('tn', '')
                for param in topic.get('parameters', []):
                    if not isinstance(param, dict):
                        continue
                    self._update_param(topic_name, param.get('pn'), param.get('v'))

        # Handle direct parameter updates
        if 'tn' in cbor_data and 'pn' in cbor_data:
            self._update_param(cbor_data['tn'], cbor_data['pn'], cbor_data.get('v'))

    def _update_param(self, topic: str, param: str, value: Any):
        """Update a specific parameter."""
        if param is None or value is None:
            return

        # Store raw for debugging
        self.raw_params[f"{topic}.{param}"] = value

        # Map to status fields
        if topic == 'Temperature':
            if param == 'CurTemp':
                self.current_room_temp = value / 10.0 if isinstance(value, (int, float)) else None
        elif topic == 'AirHeating':
            if param == 'TgtTemp':
                self.target_room_temp = value / 10.0 if isinstance(value, (int, float)) else None
            elif param == 'OpState':
                self.heating_active = value != 0
        elif topic == 'RoomClimate':
            if param == 'Mode':
                self.room_climate_mode = value
        elif topic == 'WaterHeating':
            if param == 'Mode':
                self.water_heating_mode = value
            elif param == 'CurTemp':
                self.current_water_temp = value / 10.0 if isinstance(value, (int, float)) else None
            elif param == 'TgtTemp':
                self.target_water_temp = value / 10.0 if isinstance(value, (int, float)) else None
            elif param == 'OpState':
                self.water_heating_active = value != 0
        elif topic == 'PowerSupply':
            if param == 'Volt':
                self.voltage = value / 1000.0 if isinstance(value, (int, float)) else None
        elif topic == 'EnergySrc':
            if param == 'Mode':
                self.energy_mode = value

    @property
    def room_mode_str(self) -> str:
        """Human-readable room climate mode."""
        modes = {0: 'OFF', 3: 'HEATING', 5: 'VENTILATING'}
        return modes.get(self.room_climate_mode, f'UNKNOWN({self.room_climate_mode})')

    @property
    def water_mode_str(self) -> str:
        """Human-readable water heating mode."""
        modes = {0: 'OFF', 1: 'ECO (40°C)', 2: 'HOT (60°C)'}
        return modes.get(self.water_heating_mode, f'UNKNOWN({self.water_heating_mode})')

    def display(self):
        """Print formatted status to console."""
        print("\n" + "=" * 50)
        print("        TRUMA HEATER STATUS")
        print("=" * 50)

        # Room climate
        print("\n[ROOM CLIMATE]")
        print(f"  Mode:        {self.room_mode_str}")
        if self.current_room_temp is not None:
            print(f"  Current:     {self.current_room_temp:.1f}°C")
        else:
            print(f"  Current:     --")
        if self.target_room_temp is not None:
            print(f"  Target:      {self.target_room_temp:.1f}°C")
        else:
            print(f"  Target:      --")
        print(f"  Active:      {'YES' if self.heating_active else 'NO'}")

        # Water heating
        print("\n[WATER HEATING]")
        print(f"  Mode:        {self.water_mode_str}")
        if self.current_water_temp is not None:
            print(f"  Current:     {self.current_water_temp:.1f}°C")
        else:
            print(f"  Current:     --")
        if self.target_water_temp is not None:
            print(f"  Target:      {self.target_water_temp:.1f}°C")
        else:
            print(f"  Target:      --")
        print(f"  Active:      {'YES' if self.water_heating_active else 'NO'}")

        # Power
        print("\n[POWER]")
        if self.voltage is not None:
            print(f"  Voltage:     {self.voltage:.1f}V")
        else:
            print(f"  Voltage:     --")

        # Last update
        if self.last_update:
            print(f"\nLast update: {self.last_update.strftime('%H:%M:%S')}")
        print("=" * 50)


def load_identity() -> dict:
    """Load or create persistent client identity."""
    if IDENTITY_FILE.exists():
        try:
            with open(IDENTITY_FILE) as f:
                identity = json.load(f)
                print(f"Loaded identity: {identity['muid'][:8]}...")
                return identity
        except Exception as e:
            print(f"Failed to load identity: {e}")

    # Generate new identity (matching the format from captured traffic)
    import uuid
    identity = {
        "muid": str(uuid.uuid4()).upper(),
        "uuid": str(uuid.uuid4()).lower(),
        "username": "Vanlin Controller"
    }
    save_identity(identity)
    print(f"Created new identity: {identity['muid'][:8]}...")
    return identity


def save_identity(identity: dict):
    """Save client identity for reconnection."""
    try:
        with open(IDENTITY_FILE, 'w') as f:
            json.dump(identity, f, indent=2)
    except Exception as e:
        print(f"Failed to save identity: {e}")


class TrumaController:
    """Controller for Truma iNetX."""

    def __init__(self):
        self.client = None
        self.seq = 0
        self._transport_event = None
        self._transport_ack = None  # For tracking ACK type
        self._last_notify = None
        self.status = TrumaStatus()
        self._verbose = False  # Print raw notifications
        self.identity = load_identity()  # Load persistent identity

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

    async def connect(self, address: str = None, pair: bool = False):
        """Connect to iNetX.

        Args:
            address: BLE address (auto-scans if not provided)
            pair: If True, initiate pairing after connection
        """
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
        # Some devices require pairing before connecting properly
        await self.client.connect(pair_before_connect=pair)
        print("Connected!")

        if pair:
            print("Pairing initiated during connection")

        # Discover services
        services = self.client.services
        print(f"Services discovered")

        # Enable notifications - must be done BEFORE transport layer works
        # From capture: first write 0x0100 to CCCDs (handles 0x23, 0x28)
        try:
            # Get the characteristic objects to find their CCCDs
            for char in self.client.services.characteristics.values():
                if char.uuid.lower() == CHAR_WRITE.lower():
                    # Enable notifications on CHAR_WRITE's CCCD
                    try:
                        await self.client.start_notify(char, self._on_notify)
                        print(f"Enabled notify on {char.uuid}")
                    except Exception as e:
                        print(f"Could not enable notify on CHAR_WRITE: {e}")

            await self.client.start_notify(CHAR_NOTIFY_1, self._on_notify)
            await self.client.start_notify(CHAR_NOTIFY_2, self._on_notify)
            print("Subscribed to notifications")
        except Exception as e:
            print(f"Notifications not available (pairing may be needed): {e}")

        # Run initialization handshake
        await self._init_handshake()

    async def _init_handshake(self):
        """Send initialization sequence required by iNetX (from captured traffic).

        CRITICAL: Must use persistent identity (Muid/Uuid) for reconnection to work.
        The iNetX device rejects connections from "new" clients after initial pairing.
        """
        import time

        print("Sending init handshake...")
        print(f"Using identity: {self.identity['muid'][:8]}...")

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

        # 4. Send MobileIdentity - MUST use persistent identity!
        await self._send_raw_cbor({
            'avail': 1,
            'topics': [{
                'tn': 'MobileIdentity',
                'id': 0,
                'parameters': [
                    {'v': self.identity['username'], 'id': 0, 'type': 4, 'pn': 'UserName', 'tn': 'MobileIdentity'}
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
                    {'v': self.identity['muid'], 'id': 0, 'type': 4, 'pn': 'Muid', 'tn': 'MobileIdentity'},
                    {'v': self.identity['uuid'], 'id': 0, 'type': 4, 'pn': 'Uuid', 'tn': 'MobileIdentity'}
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
        """Send a packet using transport protocol (from captured traffic).

        Transport protocol sequence:
        1. TX: 01 <len_lo> <len_hi> to CHAR_WRITE (0x22) - announce message size
        2. RX: 81 00 - ready acknowledgment
        3. TX: actual message to CHAR_WRITE_NR (0x25)
        4. RX: f0 01 - flow control / data ready
        5. RX: 83 xx 00 - message ACK with ID
        6. TX: 03 00 - confirm receipt
        """
        try:
            # 1. Announce message length
            length_announce = bytes([0x01, len(packet) & 0xFF, (len(packet) >> 8) & 0xFF])
            self._transport_event = asyncio.Event()
            self._transport_ack = None
            await self.client.write_gatt_char(CHAR_WRITE, length_announce, response=True)

            # 2. Wait for 8100 ready acknowledgment
            try:
                await asyncio.wait_for(self._transport_event.wait(), timeout=2.0)
            except asyncio.TimeoutError:
                if self._verbose:
                    print("[TRANSPORT] Timeout waiting for 8100 ACK")

            self._transport_event.clear()

            # 3. Send actual message
            await self.client.write_gatt_char(CHAR_WRITE_NR, packet, response=False)

            # 4. Wait for f001 flow control
            try:
                await asyncio.wait_for(self._transport_event.wait(), timeout=2.0)
            except asyncio.TimeoutError:
                if self._verbose:
                    print("[TRANSPORT] Timeout waiting for f001")

            self._transport_event.clear()

            # 5. Wait for 83xx00 message ACK and send 0300 confirmation
            try:
                await asyncio.wait_for(self._transport_event.wait(), timeout=2.0)
                if self._transport_ack and self._transport_ack.startswith(b'\x83'):
                    # 6. Send confirmation
                    await self.client.write_gatt_char(CHAR_WRITE, b'\x03\x00', response=True)
            except asyncio.TimeoutError:
                pass

        except Exception as e:
            if self._verbose:
                print(f"[TRANSPORT] Error: {e}")

        self._transport_event = None
        self._transport_ack = None

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

        # Handle transport layer ACKs
        if len(data) <= 4:
            self._transport_ack = data
            if self._transport_event:
                self._transport_event.set()
            if self._verbose:
                print(f"[TRANSPORT ACK] {data.hex()}")
            return

        # Signal transport event for longer messages too
        if self._transport_event:
            self._transport_event.set()

        # Try to decode CBOR from the notification
        # Header is typically 18 bytes, CBOR starts after
        cbor_data = self._decode_notification(data)
        if cbor_data:
            self.status.update_from_cbor(cbor_data)
            if self._verbose:
                print(f"[NOTIFY] {cbor_data}")

    def _decode_notification(self, data: bytes) -> dict | None:
        """Decode a notification payload containing CBOR data."""
        if len(data) < 20:
            return None

        # Try common CBOR offsets (header is typically 18 bytes)
        for offset in [18, 16, 20, 8]:
            if offset >= len(data):
                continue
            try:
                decoded = cbor2.loads(data[offset:])
                if isinstance(decoded, dict):
                    return decoded
            except Exception:
                continue

        # Fallback: scan for CBOR markers
        for i in range(len(data)):
            if data[i] in (0xbf, 0xa1, 0xa2, 0xa3, 0xa4, 0xa5, 0xa6):
                try:
                    decoded = cbor2.loads(data[i:])
                    if isinstance(decoded, dict):
                        return decoded
                except Exception:
                    continue
        return None

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


async def monitor_status(ctrl: TrumaController, interval: float = 2.0):
    """Continuously display status updates."""
    import os

    print("\nMonitoring status (Ctrl+C to stop)...")
    try:
        while True:
            # Clear screen and show status
            os.system('clear' if os.name != 'nt' else 'cls')
            ctrl.status.display()

            # Show raw params if any
            if ctrl.status.raw_params:
                print("\n[RAW PARAMETERS RECEIVED]")
                for key, value in sorted(ctrl.status.raw_params.items()):
                    print(f"  {key}: {value}")

            await asyncio.sleep(interval)
    except asyncio.CancelledError:
        pass


async def main():
    """Main entry point with CLI argument handling."""
    import argparse

    parser = argparse.ArgumentParser(description='Truma iNetX BLE Controller')
    parser.add_argument('--address', '-a', help='BLE address (auto-scan if not specified)')
    parser.add_argument('--pair', '-p', action='store_true', help='Initiate pairing with device')
    parser.add_argument('--reset-identity', action='store_true',
                        help='Reset client identity (use when re-pairing)')
    parser.add_argument('--monitor', '-m', action='store_true', help='Monitor status continuously')
    parser.add_argument('--verbose', '-v', action='store_true', help='Show raw notifications')
    parser.add_argument('--heat', type=str, choices=['on', 'off', 'vent'],
                        help='Set heating mode')
    parser.add_argument('--temp', type=float, help='Set target temperature (5-30°C)')
    parser.add_argument('--water', type=str, choices=['off', 'eco', 'hot'],
                        help='Set water heating mode')
    args = parser.parse_args()

    # Handle identity reset before anything else
    if args.reset_identity:
        if IDENTITY_FILE.exists():
            IDENTITY_FILE.unlink()
            print("Identity reset. New identity will be created on next connection.")
        else:
            print("No existing identity found.")
        if not args.pair:
            print("Use --pair with --reset-identity to establish new pairing.")
            return

    ctrl = TrumaController()
    ctrl._verbose = args.verbose

    try:
        await ctrl.connect(args.address, pair=args.pair)

        # Process commands
        if args.heat:
            mode_map = {'on': 'heating', 'off': 'off', 'vent': 'ventilating'}
            await ctrl.set_heating_mode(mode_map[args.heat])

        if args.temp:
            await ctrl.set_target_temp(args.temp)

        if args.water:
            mode_map = {'off': 'off', 'eco': 'eco', 'hot': 'comfort'}
            await ctrl.set_water_heating_mode(mode_map[args.water])

        # Either monitor or wait briefly
        if args.monitor:
            await monitor_status(ctrl)
        else:
            # Wait a bit for initial status updates
            print("\nWaiting for status updates...")
            await asyncio.sleep(3)
            ctrl.status.display()

    except KeyboardInterrupt:
        print("\nInterrupted by user")
    finally:
        await ctrl.disconnect()
        print("Disconnected.")


if __name__ == "__main__":
    asyncio.run(main())
