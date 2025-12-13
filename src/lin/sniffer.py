"""LIN bus traffic sniffer and logger."""

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, TextIO

import structlog

from .adapter import LinAdapter, TimestampedFrame
from .frame import LinFrame

log = structlog.get_logger()


@dataclass
class FrameStats:
    """Statistics for a specific frame ID."""

    frame_id: int
    count: int = 0
    last_data: bytes = b""
    last_seen: datetime | None = None
    data_changes: int = 0


@dataclass
class SnifferSession:
    """A LIN bus sniffing session with logging and analysis."""

    adapter: LinAdapter
    log_file: Path | None = None
    frame_callback: Callable[[TimestampedFrame], None] | None = None

    # Statistics per frame ID
    stats: dict[int, FrameStats] = field(default_factory=lambda: defaultdict(lambda: None))

    _running: bool = field(default=False, init=False)
    _log_handle: TextIO | None = field(default=None, init=False)

    def __post_init__(self) -> None:
        self.stats = {}

    def start(self) -> None:
        """Start the sniffing session."""
        if self.log_file:
            self._log_handle = open(self.log_file, "a")
            self._write_log_header()

        self._running = True
        log.info("Sniffer session started", log_file=str(self.log_file))

    def stop(self) -> None:
        """Stop the sniffing session."""
        self._running = False
        if self._log_handle:
            self._log_handle.close()
            self._log_handle = None
        log.info("Sniffer session stopped")

    def _write_log_header(self) -> None:
        """Write header to log file."""
        if self._log_handle:
            self._log_handle.write(f"# LIN Sniffer Log - Started {datetime.now().isoformat()}\n")
            self._log_handle.write("# Format: TIMESTAMP ID=0xXX DATA=XX XX XX... CHK=0xXX\n")
            self._log_handle.write("#\n")
            self._log_handle.flush()

    def run(self) -> None:
        """Run the sniffer, processing frames until stopped."""
        with self.adapter:
            self.start()
            try:
                for timestamped in self.adapter.read_frames():
                    if not self._running:
                        break
                    self._process_frame(timestamped)
            finally:
                self.stop()

    def _process_frame(self, timestamped: TimestampedFrame) -> None:
        """Process a received frame."""
        frame = timestamped.frame
        frame_id = frame.frame_id

        # Update statistics
        if frame_id not in self.stats:
            self.stats[frame_id] = FrameStats(frame_id=frame_id)

        stats = self.stats[frame_id]
        stats.count += 1

        if stats.last_data and stats.last_data != frame.data:
            stats.data_changes += 1

        stats.last_data = frame.data
        stats.last_seen = timestamped.timestamp

        # Log to file
        if self._log_handle:
            self._write_frame(timestamped)

        # Call user callback
        if self.frame_callback:
            self.frame_callback(timestamped)

    def _write_frame(self, timestamped: TimestampedFrame) -> None:
        """Write frame to log file."""
        frame = timestamped.frame
        ts = timestamped.timestamp.isoformat(timespec="milliseconds")
        data_hex = " ".join(f"{b:02X}" for b in frame.data)
        chk_status = "ok" if frame.verify_checksum() else "BAD"

        line = f"{ts} ID=0x{frame.frame_id:02X} DATA={data_hex} CHK=0x{frame.checksum:02X} ({chk_status})\n"

        self._log_handle.write(line)
        self._log_handle.flush()

    def print_stats(self) -> None:
        """Print session statistics."""
        print("\n=== Frame Statistics ===")
        print(f"{'ID':<8} {'Count':<10} {'Changes':<10} {'Last Data':<30}")
        print("-" * 60)

        for frame_id in sorted(self.stats.keys()):
            s = self.stats[frame_id]
            data_hex = " ".join(f"{b:02X}" for b in s.last_data) if s.last_data else "-"
            print(f"0x{s.frame_id:02X}    {s.count:<10} {s.data_changes:<10} {data_hex}")


def format_frame(timestamped: TimestampedFrame, show_raw: bool = False) -> str:
    """Format a timestamped frame for display."""
    frame = timestamped.frame
    ts = timestamped.timestamp.strftime("%H:%M:%S.%f")[:-3]
    data_hex = " ".join(f"{b:02X}" for b in frame.data)
    chk_ok = "\u2713" if frame.verify_checksum() else "\u2717"

    line = f"[{ts}] ID=0x{frame.frame_id:02X} [{data_hex}] CHK=0x{frame.checksum:02X} {chk_ok}"

    if show_raw:
        raw_hex = " ".join(f"{b:02X}" for b in timestamped.raw)
        line += f"  RAW: {raw_hex}"

    return line
