#!/usr/bin/env python3
"""Monitor all LIN frames during setpoint changes.

Captures all frame IDs and highlights when byte values change.
Run this, then change the setpoint via iNet app to find which bytes update.
"""

import sys
import time
import argparse
from collections import defaultdict
from datetime import datetime

sys.path.insert(0, 'src')
from lin.lintest_adapter import LINTestAdapter


def format_hex(data: bytes) -> str:
    """Format bytes as hex string."""
    return ' '.join(f'{b:02X}' for b in data)


def highlight_changes(old: bytes, new: bytes) -> str:
    """Format new data with changed bytes highlighted."""
    parts = []
    for i, (o, n) in enumerate(zip(old, new)):
        if o != n:
            parts.append(f'\033[91m{n:02X}\033[0m')  # Red for changes
        else:
            parts.append(f'{n:02X}')
    return ' '.join(parts)


def main():
    parser = argparse.ArgumentParser(description='Monitor LIN frames during setpoint changes')
    parser.add_argument('--port', default='/dev/tty.usbmodem11101', help='Serial port')
    parser.add_argument('--duration', type=int, default=60, help='Capture duration in seconds')
    parser.add_argument('--filter', type=str, help='Only show these frame IDs (comma-separated hex, e.g., "20,21,3C,3D")')
    args = parser.parse_args()

    # Parse filter
    frame_filter = None
    if args.filter:
        frame_filter = set(int(x.strip(), 16) for x in args.filter.split(','))

    print(f"Monitoring LIN bus for {args.duration} seconds...")
    print("Change the setpoint via iNet app during capture.")
    print("Changed bytes will be highlighted in \033[91mred\033[0m.")
    print("-" * 70)

    # Track last seen data for each frame ID
    last_data = {}
    # Track all unique values seen for each (frame_id, byte_index)
    all_values = defaultdict(lambda: defaultdict(set))
    # Count frames
    frame_counts = defaultdict(int)

    start_time = time.time()

    try:
        with LINTestAdapter(args.port, lin_baud=9600) as adapter:
            while time.time() - start_time < args.duration:
                for frame in adapter.read_frames():
                    if not frame.verify_checksum():
                        continue

                    fid = frame.frame_id
                    data = frame.data

                    # Apply filter if set
                    if frame_filter and fid not in frame_filter:
                        continue

                    frame_counts[fid] += 1

                    # Track all values seen
                    for i, b in enumerate(data):
                        all_values[fid][i].add(b)

                    # Check for changes
                    if fid in last_data:
                        old = last_data[fid]
                        if data != old:
                            ts = datetime.now().strftime('%H:%M:%S.%f')[:-3]
                            highlighted = highlight_changes(old, data)
                            print(f"{ts} [0x{fid:02X}] {highlighted}")
                    else:
                        # First time seeing this frame
                        ts = datetime.now().strftime('%H:%M:%S.%f')[:-3]
                        print(f"{ts} [0x{fid:02X}] {format_hex(data)} (first)")

                    last_data[fid] = data

                time.sleep(0.01)

    except KeyboardInterrupt:
        print("\nStopped by user")
    except Exception as e:
        print(f"Error: {e}")
        return 1

    # Print summary
    print("\n" + "=" * 70)
    print("SUMMARY - Bytes that changed during capture:")
    print("=" * 70)

    for fid in sorted(all_values.keys()):
        changes = []
        for byte_idx in sorted(all_values[fid].keys()):
            values = all_values[fid][byte_idx]
            if len(values) > 1:
                vals_str = ','.join(f'{v:02X}' for v in sorted(values)[:5])
                if len(values) > 5:
                    vals_str += f'...({len(values)} total)'
                changes.append(f"  Byte {byte_idx}: {vals_str}")

        if changes:
            print(f"\n[0x{fid:02X}] - {frame_counts[fid]} frames captured")
            for c in changes:
                print(c)

    return 0


if __name__ == '__main__':
    sys.exit(main())
