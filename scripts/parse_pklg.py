#!/usr/bin/env python3
"""Parse Apple PacketLogger (.pklg) files for BLE ATT traffic."""

import struct
import sys
from datetime import datetime

# Truma characteristic UUIDs (last 4 bytes for matching)
TRUMA_CHARS = {
    bytes.fromhex("01403114fc"): "char01",  # reversed UUID ending
    bytes.fromhex("02403114fc"): "char02",
    bytes.fromhex("03403114fc"): "char03",
    bytes.fromhex("04403114fc"): "char04",
}

def parse_pklg(filepath):
    """Parse a .pklg file and extract BLE ATT operations."""

    with open(filepath, 'rb') as f:
        data = f.read()

    print(f"Parsing {filepath} ({len(data)} bytes)")
    print("=" * 80)

    offset = 0
    packet_num = 0
    att_packets = []

    while offset < len(data) - 4:
        # Each record starts with 4-byte length
        if offset + 4 > len(data):
            break

        record_len = struct.unpack('<I', data[offset:offset+4])[0]

        if record_len == 0 or record_len > 65535:
            offset += 1
            continue

        if offset + 4 + record_len > len(data):
            break

        record = data[offset+4:offset+4+record_len]
        offset += 4 + record_len
        packet_num += 1

        # Look for ATT protocol data
        # ATT opcodes we care about:
        # 0x12 = Write Request
        # 0x52 = Write Command (no response)
        # 0x1B = Handle Value Notification
        # 0x1D = Handle Value Indication

        record_hex = record.hex()

        # Search for fc314 UUID pattern (Truma service)
        if 'fc314' in record_hex or 'fc3140' in record_hex:
            # Found Truma-related packet
            att_packets.append((packet_num, record))

        # Also look for ATT write/notify opcodes with reasonable handle values
        for i in range(len(record) - 3):
            opcode = record[i]
            if opcode in (0x12, 0x52, 0x1B, 0x1D):
                # Potential ATT packet
                handle = struct.unpack('<H', record[i+1:i+3])[0] if i+3 <= len(record) else 0
                if 0x0001 <= handle <= 0x00FF:  # Reasonable handle range
                    payload = record[i+3:] if i+3 < len(record) else b''
                    if len(payload) > 0 and len(payload) < 200:
                        att_packets.append((packet_num, record, i, opcode, handle, payload))

    print(f"Found {len(att_packets)} potential ATT packets")
    print()

    # Output findings
    seen = set()
    for item in att_packets:
        if len(item) == 2:
            pkt_num, record = item
            if record.hex() not in seen:
                seen.add(record.hex())
                print(f"Packet {pkt_num}: {record.hex()[:100]}...")
        elif len(item) == 6:
            pkt_num, record, pos, opcode, handle, payload = item

            opcode_names = {
                0x12: "WRITE_REQ",
                0x52: "WRITE_CMD",
                0x1B: "NOTIFY",
                0x1D: "INDICATE"
            }

            key = (opcode, handle, payload.hex())
            if key not in seen:
                seen.add(key)
                op_name = opcode_names.get(opcode, f"0x{opcode:02x}")
                direction = ">>>" if opcode in (0x12, 0x52) else "<<<"
                print(f"{direction} {op_name} handle=0x{handle:04x} data={payload.hex()}")


def extract_att_data(filepath):
    """More targeted extraction looking for specific patterns."""

    with open(filepath, 'rb') as f:
        data = f.read()

    print(f"\nSearching for Truma BLE data patterns...")
    print("=" * 80)

    # Look for the Truma service UUID: fc314000-f3b2-11e8-8eb2-f2801f1b9fd1
    # In BLE, UUIDs are stored in little-endian, so we search for reversed patterns

    # The characteristic base is fc31400X where X is 1-4
    # In little endian bytes: d19f1b1f-80f2-b28e-e811-b2f3-0040-31fc

    patterns_found = []

    # Search for write patterns - look for sequences that might be commands
    # Truma commands often start with specific bytes based on the protocol

    # Let's look for any 20-byte sequences that could be BLE packets
    for i in range(len(data) - 20):
        chunk = data[i:i+24]

        # Look for potential ATT headers followed by data
        # ATT Write Request: 0x12 <handle_lo> <handle_hi> <data...>
        # ATT Notification:  0x1b <handle_lo> <handle_hi> <data...>

        if chunk[0] == 0x12 or chunk[0] == 0x1b or chunk[0] == 0x52:
            handle = struct.unpack('<H', chunk[1:3])[0]
            if 0x10 <= handle <= 0x50:  # Reasonable GATT handle range
                payload = chunk[3:23]
                if any(b != 0 for b in payload[:10]):  # Has some data
                    direction = "TX" if chunk[0] in (0x12, 0x52) else "RX"
                    op = {0x12: "WRITE", 0x52: "WRITE_NR", 0x1b: "NOTIFY"}.get(chunk[0], "?")
                    patterns_found.append((i, direction, op, handle, payload))

    # Deduplicate and print
    seen = set()
    for offset, direction, op, handle, payload in patterns_found:
        key = (direction, handle, payload.hex())
        if key not in seen:
            seen.add(key)
            print(f"[{direction}] {op:8} h=0x{handle:02x} | {payload.hex()}")

    print(f"\nFound {len(seen)} unique ATT-like patterns")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python parse_pklg.py <file.pklg>")
        sys.exit(1)

    filepath = sys.argv[1]
    parse_pklg(filepath)
    extract_att_data(filepath)
