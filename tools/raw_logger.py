#!/usr/bin/env python3
"""Raw LIN bus traffic logger.

Simple CLI tool for capturing and logging LIN bus traffic.
Use this for initial reverse-engineering and protocol analysis.

Usage:
    python -m tools.raw_logger --port /dev/ttyUSB0
    python -m tools.raw_logger --port /dev/ttyUSB0 --baud 19200 --log traffic.log
"""

import argparse
import signal
import sys
from datetime import datetime
from pathlib import Path

import structlog

from src.lin import SerialLinAdapter, ChecksumType, format_frame, SnifferSession

# Configure structured logging
structlog.configure(
    processors=[
        structlog.stdlib.add_log_level,
        structlog.dev.ConsoleRenderer(colors=True),
    ],
    wrapper_class=structlog.make_filtering_bound_logger(20),  # INFO level
)

log = structlog.get_logger()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="LIN bus traffic logger for Truma InetX reverse engineering"
    )
    parser.add_argument(
        "--port",
        "-p",
        required=True,
        help="Serial port (e.g., /dev/ttyUSB0, COM3)",
    )
    parser.add_argument(
        "--baud",
        "-b",
        type=int,
        default=9600,
        help="Baud rate (default: 9600 - Truma standard)",
    )
    parser.add_argument(
        "--log",
        "-l",
        type=Path,
        help="Log file path (optional)",
    )
    parser.add_argument(
        "--checksum",
        "-c",
        choices=["classic", "enhanced"],
        default="enhanced",
        help="Checksum type (default: enhanced)",
    )
    parser.add_argument(
        "--raw",
        "-r",
        action="store_true",
        help="Show raw bytes in output",
    )
    parser.add_argument(
        "--quiet",
        "-q",
        action="store_true",
        help="Only log to file, no console output",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    checksum_type = (
        ChecksumType.CLASSIC if args.checksum == "classic" else ChecksumType.ENHANCED
    )

    # Set up log file
    log_file = args.log
    if not log_file:
        # Default log file with timestamp
        logs_dir = Path(__file__).parent.parent / "logs"
        logs_dir.mkdir(exist_ok=True)
        log_file = logs_dir / f"capture_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"

    print(f"LIN Bus Logger")
    print(f"==============")
    print(f"Port:     {args.port}")
    print(f"Baud:     {args.baud}")
    print(f"Checksum: {args.checksum}")
    print(f"Log file: {log_file}")
    print()
    print("Press Ctrl+C to stop\n")

    # Create adapter
    adapter = SerialLinAdapter(
        port=args.port,
        baudrate=args.baud,
        checksum_type=checksum_type,
    )

    # Frame callback for console output
    def on_frame(timestamped):
        if not args.quiet:
            print(format_frame(timestamped, show_raw=args.raw))

    # Create session
    session = SnifferSession(
        adapter=adapter,
        log_file=log_file,
        frame_callback=on_frame,
    )

    # Handle Ctrl+C gracefully
    def signal_handler(sig, frame):
        print("\n\nStopping...")
        session.stop()

    signal.signal(signal.SIGINT, signal_handler)

    try:
        session.run()
    except Exception as e:
        log.error("Error during capture", error=str(e))
        return 1
    finally:
        session.print_stats()
        print(f"\nLog saved to: {log_file}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
