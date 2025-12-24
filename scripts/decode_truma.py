#!/usr/bin/env python3
"""Decode Truma iNetX BLE protocol from PacketLogger captures."""

import struct
import sys
import cbor2
from io import BytesIO

def find_cbor_in_payload(data: bytes) -> list:
    """Try to find and decode CBOR data within a payload."""
    results = []

    # The Truma protocol seems to have a header before CBOR
    # Look for CBOR markers: bf (map), 9f (array), a0-bf (small maps)

    for i in range(len(data)):
        if data[i] in (0xbf, 0x9f, 0xa1, 0xa2, 0xa3, 0xa4, 0xa5, 0xa6):
            try:
                decoded = cbor2.loads(data[i:])
                results.append((i, decoded))
                break  # Usually just one CBOR object
            except:
                continue

    return results


def parse_truma_message(payload: bytes) -> dict:
    """Parse a Truma protocol message."""

    if len(payload) < 16:
        return {"raw": payload.hex()}

    # The header appears to be ~16 bytes before CBOR data
    # Format seems to be: flags(2) seq(2) len(2) type(2) + reserved(8) + cbor

    result = {
        "header": payload[:16].hex(),
        "raw": payload.hex()
    }

    # Try to decode CBOR from various offsets
    for offset in [16, 18, 20, 8, 10, 12]:
        if offset < len(payload):
            try:
                decoded = cbor2.loads(payload[offset:])
                result["cbor"] = decoded
                result["cbor_offset"] = offset
                break
            except:
                continue

    # Also try finding CBOR markers
    cbor_results = find_cbor_in_payload(payload)
    if cbor_results and "cbor" not in result:
        result["cbor_offset"], result["cbor"] = cbor_results[0]

    return result


def extract_att_packets(filepath: str) -> list:
    """Extract ATT packets from a .pklg file."""

    with open(filepath, 'rb') as f:
        data = f.read()

    packets = []
    offset = 0

    while offset < len(data) - 4:
        record_len = struct.unpack('<I', data[offset:offset+4])[0]

        if record_len == 0 or record_len > 65535:
            offset += 1
            continue

        if offset + 4 + record_len > len(data):
            break

        record = data[offset+4:offset+4+record_len]
        offset += 4 + record_len

        # Look for ATT packets with handles in Truma range (0x20-0x30)
        for i in range(len(record) - 3):
            opcode = record[i]
            if opcode in (0x12, 0x52, 0x1B, 0x1D):  # Write/Notify opcodes
                handle = struct.unpack('<H', record[i+1:i+3])[0]
                if 0x20 <= handle <= 0x30:  # Truma GATT handle range
                    payload = record[i+3:]
                    if len(payload) > 10:
                        packets.append({
                            "opcode": opcode,
                            "handle": handle,
                            "direction": "TX" if opcode in (0x12, 0x52) else "RX",
                            "payload": payload
                        })
                        break

    return packets


def main():
    if len(sys.argv) < 2:
        print("Usage: python decode_truma.py <file.pklg>")
        sys.exit(1)

    filepath = sys.argv[1]
    print(f"Decoding {filepath}...")
    print("=" * 80)

    packets = extract_att_packets(filepath)
    print(f"Found {len(packets)} Truma ATT packets\n")

    seen_topics = set()
    seen_params = set()

    for i, pkt in enumerate(packets):
        direction = pkt["direction"]
        handle = pkt["handle"]
        payload = pkt["payload"]

        parsed = parse_truma_message(payload)

        if "cbor" in parsed:
            cbor_data = parsed["cbor"]

            # Extract topic names
            if isinstance(cbor_data, dict):
                if "tn" in cbor_data:  # topic name
                    seen_topics.add(cbor_data["tn"])
                if "topics" in cbor_data:
                    for topic in cbor_data["topics"]:
                        if isinstance(topic, dict) and "tn" in topic:
                            seen_topics.add(topic["tn"])
                        if isinstance(topic, dict) and "parameters" in topic:
                            for param in topic["parameters"]:
                                if isinstance(param, dict) and "pn" in param:
                                    seen_params.add((topic.get("tn", "?"), param["pn"]))

            print(f"[{direction}] h=0x{handle:02x}")
            print(f"    CBOR: {cbor_data}")
            print()

    print("=" * 80)
    print("DISCOVERED TOPICS:")
    for topic in sorted(seen_topics):
        print(f"  - {topic}")

    print("\nDISCOVERED PARAMETERS:")
    for topic, param in sorted(seen_params):
        print(f"  - {topic}.{param}")


if __name__ == "__main__":
    main()
