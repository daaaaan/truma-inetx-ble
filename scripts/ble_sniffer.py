#!/usr/bin/env python3
"""Direct BLE sniffer for Truma iNetX.

Connects directly to the iNetX and logs all notifications.
Can also send commands interactively.
"""

import asyncio
import sys
from datetime import datetime
from typing import Optional

from bleak import BleakClient, BleakScanner

# Truma service/characteristic UUIDs
SERVICE_UUID = "fc314000-f3b2-11e8-8eb2-f2801f1b9fd1"
CHAR_1_UUID = "fc314001-f3b2-11e8-8eb2-f2801f1b9fd1"  # notify, write
CHAR_2_UUID = "fc314002-f3b2-11e8-8eb2-f2801f1b9fd1"  # write-without-response
CHAR_3_UUID = "fc314003-f3b2-11e8-8eb2-f2801f1b9fd1"  # notify
CHAR_4_UUID = "fc314004-f3b2-11e8-8eb2-f2801f1b9fd1"  # notify

log_file = None


def log(direction: str, char: str, data: bytes):
    """Log traffic with timestamp."""
    ts = datetime.now().strftime('%H:%M:%S.%f')[:-3]
    hex_data = data.hex()

    # Try to decode as ASCII for readability
    try:
        ascii_repr = ''.join(chr(b) if 32 <= b < 127 else '.' for b in data)
    except:
        ascii_repr = ""

    msg = f"{ts} [{direction:4}] char{char}: {hex_data}"
    if ascii_repr:
        msg += f"  |{ascii_repr}|"

    print(msg)
    if log_file:
        log_file.write(msg + "\n")
        log_file.flush()


def make_handler(char_uuid: str):
    """Create notification handler for a characteristic."""
    char_num = char_uuid[-4:-2]  # Get "01", "03", or "04"

    def handler(sender, data: bytes):
        log("RECV", char_num, data)

    return handler


async def send_command(client: BleakClient, hex_str: str, char: str = "01"):
    """Send a hex command to the device."""
    try:
        data = bytes.fromhex(hex_str.replace(" ", ""))
        char_uuid = f"fc31400{char}-f3b2-11e8-8eb2-f2801f1b9fd1"

        if char == "02":
            await client.write_gatt_char(char_uuid, data, response=False)
        else:
            await client.write_gatt_char(char_uuid, data, response=True)

        log("SEND", char, data)
    except ValueError as e:
        print(f"Invalid hex: {e}")
    except Exception as e:
        print(f"Send error: {e}")


async def main():
    global log_file

    log_file = open("ble_sniffer.log", "w")
    print("Logging to ble_sniffer.log")
    print()

    # Find iNetX
    print("Scanning for iNetX...")
    devices = await BleakScanner.discover(timeout=5.0)
    inetx = next((d for d in devices if d.name and 'inetx' in d.name.lower()), None)

    if not inetx:
        print("iNetX not found! Make sure it's powered on and not connected to another device.")
        return

    print(f"Found: {inetx.name} ({inetx.address})")
    print()

    # Connect
    print("Connecting...")
    async with BleakClient(inetx.address, timeout=30.0) as client:
        print(f"Connected!")
        print()

        # Subscribe to all notify characteristics
        await client.start_notify(CHAR_1_UUID, make_handler(CHAR_1_UUID))
        await client.start_notify(CHAR_3_UUID, make_handler(CHAR_3_UUID))
        await client.start_notify(CHAR_4_UUID, make_handler(CHAR_4_UUID))
        print("Subscribed to notifications on char01, char03, char04")
        print()
        print("=" * 70)
        print("SNIFFER RUNNING - All notifications will be logged")
        print()
        print("Commands:")
        print("  <hex>        - Send hex to char01 (e.g., 0d0a)")
        print("  2:<hex>      - Send hex to char02 (write-without-response)")
        print("  q            - Quit")
        print("=" * 70)
        print()

        # Read input and send commands
        loop = asyncio.get_event_loop()

        while True:
            try:
                # Non-blocking input
                line = await loop.run_in_executor(None, sys.stdin.readline)
                line = line.strip()

                if not line:
                    continue

                if line.lower() == 'q':
                    print("Quitting...")
                    break

                # Parse command
                if line.startswith("2:"):
                    await send_command(client, line[2:], "02")
                else:
                    await send_command(client, line, "01")

            except KeyboardInterrupt:
                print("\nQuitting...")
                break
            except Exception as e:
                print(f"Error: {e}")

    log_file.close()
    print("\nDisconnected. Log saved to ble_sniffer.log")


if __name__ == "__main__":
    asyncio.run(main())
