#!/usr/bin/env python3
"""BLE Bridge/MITM for Truma iNetX.

Advertises as fake iNetX, connects to real one, forwards and logs all traffic.
"""

import asyncio
import sys
from datetime import datetime
from typing import Optional

from bleak import BleakClient, BleakScanner
from bless import BlessServer, BlessGATTCharacteristic, GATTCharacteristicProperties, GATTAttributePermissions

# Truma service/characteristic UUIDs
SERVICE_UUID = "fc314000-f3b2-11e8-8eb2-f2801f1b9fd1"
CHAR_1_UUID = "fc314001-f3b2-11e8-8eb2-f2801f1b9fd1"  # notify, write
CHAR_2_UUID = "fc314002-f3b2-11e8-8eb2-f2801f1b9fd1"  # write-without-response
CHAR_3_UUID = "fc314003-f3b2-11e8-8eb2-f2801f1b9fd1"  # notify
CHAR_4_UUID = "fc314004-f3b2-11e8-8eb2-f2801f1b9fd1"  # notify

# Global state
real_client: Optional[BleakClient] = None
server: Optional[BlessServer] = None
log_file = None


def log(direction: str, char: str, data: bytes):
    """Log traffic."""
    ts = datetime.now().strftime('%H:%M:%S.%f')[:-3]
    hex_data = data.hex()
    msg = f"{ts} [{direction}] {char}: {hex_data}"
    print(msg)
    if log_file:
        log_file.write(msg + "\n")
        log_file.flush()


# Handlers for real iNetX notifications -> forward to phone
def make_real_handler(char_uuid: str):
    async def handler(sender, data: bytes):
        log("iNetX->Phone", char_uuid[-4:], data)
        if server:
            try:
                server.get_characteristic(char_uuid).value = data
                await server.update_value(SERVICE_UUID, char_uuid)
            except Exception as e:
                print(f"Forward error: {e}")
    return handler


# Handler for phone writes -> forward to real iNetX
async def on_phone_write(char: BlessGATTCharacteristic, data: bytes):
    """Called when phone writes to our fake iNetX."""
    char_uuid = str(char.uuid)
    log("Phone->iNetX", char_uuid[-4:], data)

    if real_client and real_client.is_connected:
        try:
            # Forward to real device
            response = "write" in char.properties
            await real_client.write_gatt_char(char_uuid, data, response=response)
        except Exception as e:
            print(f"Forward to iNetX error: {e}")


async def main():
    global real_client, server, log_file

    # Open log file
    log_file = open("ble_bridge.log", "w")
    print("Logging to ble_bridge.log")

    # Find real iNetX
    print("Scanning for real iNetX...")
    devices = await BleakScanner.discover(timeout=5.0)
    real_inetx = next((d for d in devices if d.name and 'inetx' in d.name.lower()), None)

    if not real_inetx:
        print("Real iNetX not found!")
        return

    print(f"Found real iNetX: {real_inetx.name} ({real_inetx.address})")
    real_name = real_inetx.name  # Save for spoofing

    # Connect to real iNetX (with retry)
    print("Connecting to real iNetX...")
    print("(Make sure no phone/tablet is connected to it!)")
    real_client = BleakClient(real_inetx.address, timeout=30.0)

    for attempt in range(3):
        try:
            await real_client.connect()
            print("Connected to real iNetX!")
            break
        except Exception as e:
            print(f"Connection attempt {attempt+1} failed: {e}")
            if attempt < 2:
                print("Retrying in 3 seconds...")
                await asyncio.sleep(3)
            else:
                print("\nFailed to connect. Make sure:")
                print("  1. The iNetX is NOT connected to your phone")
                print("  2. Forget the iNetX in your phone's Bluetooth settings")
                print("  3. The iNetX is powered on and in range")
                return

    # Subscribe to real iNetX notifications
    await real_client.start_notify(CHAR_1_UUID, make_real_handler(CHAR_1_UUID))
    await real_client.start_notify(CHAR_3_UUID, make_real_handler(CHAR_3_UUID))
    await real_client.start_notify(CHAR_4_UUID, make_real_handler(CHAR_4_UUID))
    print("Subscribed to real iNetX notifications")

    # Create fake iNetX server (use real device name for better app compatibility)
    print()
    print(f"Starting fake iNetX server as '{real_name}'...")
    server = BlessServer(name=real_name)

    await server.add_new_service(SERVICE_UUID)

    # Add characteristics matching real iNetX
    await server.add_new_characteristic(
        SERVICE_UUID, CHAR_1_UUID,
        GATTCharacteristicProperties.notify | GATTCharacteristicProperties.write,
        None,
        GATTAttributePermissions.readable | GATTAttributePermissions.writeable
    )
    await server.add_new_characteristic(
        SERVICE_UUID, CHAR_2_UUID,
        GATTCharacteristicProperties.write_without_response,
        None,
        GATTAttributePermissions.writeable
    )
    await server.add_new_characteristic(
        SERVICE_UUID, CHAR_3_UUID,
        GATTCharacteristicProperties.notify,
        None,
        GATTAttributePermissions.readable
    )
    await server.add_new_characteristic(
        SERVICE_UUID, CHAR_4_UUID,
        GATTCharacteristicProperties.notify,
        None,
        GATTAttributePermissions.readable
    )

    # Set write handler
    server.write_request_func = lambda char, data: asyncio.create_task(on_phone_write(char, data))

    await server.start()
    print()
    print("=" * 60)
    print("BRIDGE RUNNING!")
    print(f"Advertising as: {real_name}")
    print()
    print("1. Forget the real iNetX on your phone (Bluetooth settings)")
    print("2. Open Truma app and let it discover the bridge")
    print("3. All traffic will be logged to ble_bridge.log")
    print("=" * 60)
    print()
    print("Press Ctrl+C to stop")
    print()

    try:
        while True:
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        print("\nStopping...")
    finally:
        await server.stop()
        await real_client.disconnect()
        log_file.close()


if __name__ == "__main__":
    asyncio.run(main())
