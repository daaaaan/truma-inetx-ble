"""USB-LIN adapter interfaces."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Iterator

import serial
import structlog

from .frame import ChecksumType, LinFrame

log = structlog.get_logger()


@dataclass
class TimestampedFrame:
    """A LIN frame with capture timestamp."""

    timestamp: datetime
    frame: LinFrame
    raw: bytes


class LinAdapter(ABC):
    """Abstract base class for LIN bus adapters."""

    @abstractmethod
    def open(self) -> None:
        """Open connection to the adapter."""

    @abstractmethod
    def close(self) -> None:
        """Close connection to the adapter."""

    @abstractmethod
    def read_frames(self) -> Iterator[TimestampedFrame]:
        """Yield LIN frames as they arrive."""

    @abstractmethod
    def send_frame(self, frame: LinFrame) -> None:
        """Send a LIN frame."""

    def __enter__(self) -> "LinAdapter":
        self.open()
        return self

    def __exit__(self, *args) -> None:
        self.close()


class SerialLinAdapter(LinAdapter):
    """Serial-based USB-LIN adapter.

    This is a generic implementation that assumes the adapter sends raw LIN
    frames over serial. The exact framing protocol may need adjustment based
    on your specific adapter.

    Common adapter behaviors:
    - Some send raw bytes with break as a special character
    - Some wrap frames in a protocol (STX/ETX, length prefix, etc.)
    - Some use AT commands for configuration

    We'll start with a simple raw byte approach and adjust as needed.
    """

    # LIN break is typically detected as a framing error or 0x00
    BREAK_BYTE = 0x00

    def __init__(
        self,
        port: str,
        baudrate: int = 9600,  # Truma uses 9600, not typical 19200
        checksum_type: ChecksumType = ChecksumType.ENHANCED,
        timeout: float = 1.0,
    ) -> None:
        self.port = port
        self.baudrate = baudrate
        self.checksum_type = checksum_type
        self.timeout = timeout
        self._serial: serial.Serial | None = None
        self._buffer = bytearray()

    def open(self) -> None:
        """Open the serial connection."""
        log.info("Opening serial port", port=self.port, baudrate=self.baudrate)
        self._serial = serial.Serial(
            port=self.port,
            baudrate=self.baudrate,
            timeout=self.timeout,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_TWO,  # Truma uses 2 stop bits
        )
        self._buffer.clear()

    def close(self) -> None:
        """Close the serial connection."""
        if self._serial and self._serial.is_open:
            log.info("Closing serial port")
            self._serial.close()
            self._serial = None

    def read_frames(self) -> Iterator[TimestampedFrame]:
        """Yield LIN frames from the serial port.

        This implementation assumes a simple protocol where:
        - Break is represented as 0x00 (or framing error)
        - Sync byte (0x55) follows break
        - PID, data, and checksum follow

        May need adjustment based on actual adapter behavior.
        """
        if not self._serial:
            raise RuntimeError("Adapter not open")

        while True:
            # Read available bytes
            if self._serial.in_waiting:
                new_data = self._serial.read(self._serial.in_waiting)
                self._buffer.extend(new_data)

            # Try to extract frames from buffer
            frame = self._try_parse_frame()
            if frame:
                yield frame

    def _try_parse_frame(self) -> TimestampedFrame | None:
        """Try to parse a complete frame from the buffer.

        Returns None if no complete frame is available.
        """
        # Look for sync byte (0x55)
        try:
            sync_pos = self._buffer.index(LinFrame.SYNC_BYTE)
        except ValueError:
            # No sync byte found, discard buffer
            if len(self._buffer) > 100:
                log.debug("Discarding buffer, no sync found", size=len(self._buffer))
                self._buffer.clear()
            return None

        # Discard bytes before sync
        if sync_pos > 0:
            discarded = bytes(self._buffer[:sync_pos])
            log.debug("Discarding bytes before sync", discarded=discarded.hex())
            del self._buffer[:sync_pos]

        # Need at least SYNC + PID + CHECKSUM (3 bytes minimum)
        if len(self._buffer) < 3:
            return None

        # We don't know data length yet - LIN doesn't encode it in the frame
        # Common Truma frames seem to be 8 bytes data
        # We'll try different lengths and see which checksum validates
        for data_len in [8, 4, 2, 1, 0]:
            frame_len = 2 + data_len + 1  # SYNC + PID + DATA + CHECKSUM

            if len(self._buffer) < frame_len:
                continue

            raw = bytes(self._buffer[:frame_len])
            try:
                frame = LinFrame.from_bytes(
                    raw,
                    checksum_type=self.checksum_type,
                    validate=True,
                )
                # Valid frame found
                timestamp = datetime.now()
                del self._buffer[:frame_len]

                log.debug(
                    "Frame parsed",
                    frame_id=f"0x{frame.frame_id:02X}",
                    data_len=len(frame.data),
                )

                return TimestampedFrame(timestamp=timestamp, frame=frame, raw=raw)

            except ValueError:
                # Invalid frame at this length, try next
                continue

        # No valid frame found at any length
        # If buffer is getting large, discard the sync and look for next one
        if len(self._buffer) > 20:
            log.debug("No valid frame found, discarding sync byte")
            del self._buffer[0]

        return None

    def send_frame(self, frame: LinFrame) -> None:
        """Send a LIN frame.

        Note: Sending requires the adapter to act as LIN master.
        The exact protocol depends on your adapter.
        """
        if not self._serial:
            raise RuntimeError("Adapter not open")

        # Most adapters expect: BREAK + frame bytes
        # Some adapters handle break generation automatically
        # This may need adjustment for your specific adapter
        data = frame.to_bytes()
        log.info("Sending frame", frame_id=f"0x{frame.frame_id:02X}")
        self._serial.write(data)


class MockLinAdapter(LinAdapter):
    """Mock adapter for testing without hardware."""

    def __init__(self, frames: list[LinFrame] | None = None) -> None:
        self.frames = frames or []
        self._index = 0
        self._is_open = False

    def open(self) -> None:
        self._is_open = True
        self._index = 0

    def close(self) -> None:
        self._is_open = False

    def read_frames(self) -> Iterator[TimestampedFrame]:
        while self._index < len(self.frames):
            frame = self.frames[self._index]
            self._index += 1
            yield TimestampedFrame(
                timestamp=datetime.now(),
                frame=frame,
                raw=frame.to_bytes(),
            )

    def send_frame(self, frame: LinFrame) -> None:
        self.frames.append(frame)
