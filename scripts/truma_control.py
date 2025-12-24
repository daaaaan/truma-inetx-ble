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

    def _build_command(self, topic: str, param: str, value, param_type: int = 2) -> bytes:
        """Build a command packet.

        Args:
            topic: Topic name (e.g., 'RoomClimate', 'AirHeating')
            param: Parameter name (e.g., 'Mode', 'TgtTemp')
            value: Value to set
            param_type: Parameter type code
        """
        self.seq += 1

        # CBOR payload - map with 4 keys (matching captured format)
        # Order matters: pn, tn, v, id
        payload = {
            'pn': param,
            'tn': topic,
            'v': value,
            'id': 0,
        }
        cbor_map = cbor2.dumps(payload)

        # Append "type" string + integer (separate CBOR items after the map)
        type_cbor = cbor2.dumps('type') + cbor2.dumps(param_type)

        cbor_data = cbor_map + type_cbor

        # Header (18 bytes)
        header = bytearray(18)
        header[0] = 0x01
        header[1] = self.seq & 0xFF
        header[2] = 0x00
        header[3] = 0x05
        # Length at bytes 4-5 (CBOR length + 2 for the 01 00 message type)
        total_len = len(cbor_data) + 2
        struct.pack_into('<H', header, 4, total_len)
        header[6] = 0x03
        header[7] = 0x00
        # Bytes 8-15 are zeros (padding)
        header[16] = 0x01
        header[17] = 0x00

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

        # Subscribe to notifications
        await self.client.start_notify(CHAR_NOTIFY_1, self._on_notify)
        await self.client.start_notify(CHAR_NOTIFY_2, self._on_notify)

    def _on_notify(self, sender, data: bytes):
        """Handle notifications from device."""
        print(f"[NOTIFY] {data.hex()[:60]}...")

    async def disconnect(self):
        """Disconnect from device."""
        if self.client:
            await self.client.disconnect()

    async def send_command(self, topic: str, param: str, value, param_type: int = 2):
        """Send a control command."""
        cmd = self._build_command(topic, param, value, param_type)
        print(f"Sending: {topic}.{param} = {value}")
        print(f"  Packet: {cmd.hex()}")
        await self.client.write_gatt_char(CHAR_WRITE_NR, cmd, response=False)
        await asyncio.sleep(0.5)  # Wait for response

    # High-level commands
    # Type codes discovered from capture:
    # - Mode (enum): type=2 (0x02)
    # - TgtTemp (int16): type=10 (0x0a) but sent as 106 (0x6a)?
    # - Active (bool): type=107 (0x6b)

    async def set_heating_mode(self, mode: str):
        """Set room climate mode.

        Args:
            mode: 'off', 'heating', or 'ventilating'
        """
        modes = {'off': 0, 'heating': 3, 'ventilating': 5}
        value = modes.get(mode.lower(), 0)
        await self.send_command('RoomClimate', 'Mode', value, param_type=0x66)  # 102

    async def set_target_temp(self, temp_c: float):
        """Set target temperature in Celsius (for AirHeating).

        Args:
            temp_c: Temperature in Celsius (e.g., 20.0 for 20°C)
                    Range: 5.0 to 30.0
        """
        value = int(temp_c * 10)  # Convert to decicelsius
        await self.send_command('AirHeating', 'TgtTemp', value, param_type=0x6a)  # 106

    async def set_water_heating_mode(self, mode: str):
        """Set water heating mode.

        Args:
            mode: '40', '60', or '70' (temperature in °C)
        """
        modes = {'40': 0, '60': 1, '70': 2}
        value = modes.get(mode, 0)
        await self.send_command('WaterHeating', 'Mode', value, param_type=2)


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
