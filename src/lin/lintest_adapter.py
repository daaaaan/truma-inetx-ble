"""LINTest-MI adapter driver.

Protocol discovered from: https://github.com/VoLinhTruc/LINTestMI_bico

Frame format (16 bytes):
- Byte 0: Header (0x11=mode cmd, 0x44=monitor response)
- Byte 1: Channel
- Byte 2: Frame ID
- Byte 3: Direction (0=send, 1=receive)
- Byte 4: Checksum type (0=error, 1=classic, 2=enhanced)
- Byte 5: Data length
- Bytes 6-13: Data (up to 8 bytes)
- Byte 14: LIN checksum
- Byte 15: Frame checksum
"""

import serial
import time
from typing import Iterator, Optional
from dataclasses import dataclass

from .frame import LinFrame, ChecksumType
from .adapter import LinAdapter


@dataclass
class LINTestFrame:
    """Raw frame from LINTest-MI adapter."""
    header: int
    channel: int
    frame_id: int
    direction: int
    checksum_type: int
    length: int
    data: bytes
    lin_checksum: int
    frame_checksum: int
    raw: bytes

    def to_lin_frame(self) -> Optional[LinFrame]:
        """Convert to standard LinFrame if valid."""
        if self.checksum_type == 0:  # Error
            return None
        cs_type = ChecksumType.ENHANCED if self.checksum_type == 2 else ChecksumType.CLASSIC
        return LinFrame(
            frame_id=self.frame_id,
            data=self.data[:self.length],
            checksum=self.lin_checksum,
            checksum_type=cs_type
        )


def calc_frame_checksum(data: bytes) -> int:
    """Calculate LINTest-MI frame checksum (two's complement)."""
    s = sum(data) & 0xFF
    return ((~s) + 1) & 0xFF


def verify_frame_checksum(data: bytes) -> bool:
    """Verify 16-byte frame checksum."""
    if len(data) != 16:
        return False
    expected = calc_frame_checksum(data[:15])
    return data[15] == expected


def parse_frame(data: bytes) -> Optional[LINTestFrame]:
    """Parse 16-byte frame from adapter."""
    if len(data) != 16:
        return None
    if not verify_frame_checksum(data):
        return None

    return LINTestFrame(
        header=data[0],
        channel=data[1],
        frame_id=data[2],
        direction=data[3],
        checksum_type=data[4],
        length=data[5],
        data=bytes(data[6:14]),
        lin_checksum=data[14],
        frame_checksum=data[15],
        raw=bytes(data)
    )


class LINTestAdapter(LinAdapter):
    """Driver for LINTest-MI USB-LIN adapter."""

    HEADER_MODE = 0x11
    HEADER_MONITOR = 0x44

    MODE_STANDBY = 0
    MODE_HOST = 1
    MODE_SLAVE = 2
    MODE_MONITOR = 3

    def __init__(self, port: str, lin_baud: int = 9600):
        self.port = port
        self.lin_baud = lin_baud
        self.usb_baud = 460800
        self._serial: Optional[serial.Serial] = None
        self._buffer = bytearray()

    def _build_mode_cmd(self, mode: int, baud: int) -> bytes:
        """Build 16-byte mode command."""
        cmd = bytearray(16)
        cmd[0] = self.HEADER_MODE
        cmd[1] = mode
        cmd[2] = (baud >> 8) & 0xFF
        cmd[3] = baud & 0xFF
        cmd[15] = calc_frame_checksum(cmd[:15])
        return bytes(cmd)

    def open(self) -> None:
        """Open adapter and set monitor mode."""
        self._serial = serial.Serial(
            self.port,
            self.usb_baud,
            timeout=0.1
        )
        self._serial.reset_input_buffer()
        self._serial.reset_output_buffer()
        time.sleep(0.1)

        # Must send standby first, then monitor
        standby_cmd = self._build_mode_cmd(self.MODE_STANDBY, self.lin_baud)
        self._serial.write(standby_cmd)
        self._serial.flush()
        time.sleep(0.1)
        self._serial.read(1000)  # Drain any response

        monitor_cmd = self._build_mode_cmd(self.MODE_MONITOR, self.lin_baud)
        self._serial.write(monitor_cmd)
        self._serial.flush()
        time.sleep(0.1)
        self._serial.read(1000)  # Drain initial burst

    def close(self) -> None:
        """Close adapter."""
        if self._serial:
            # Return to standby
            try:
                cmd = self._build_mode_cmd(self.MODE_STANDBY, self.lin_baud)
                self._serial.write(cmd)
                self._serial.flush()
            except:
                pass
            self._serial.close()
            self._serial = None

    def read_frames(self) -> Iterator[LinFrame]:
        """Read and yield LIN frames."""
        if not self._serial:
            return

        # Read available data
        data = self._serial.read(256)
        if data:
            self._buffer.extend(data)

        # Process complete 16-byte frames
        while len(self._buffer) >= 16:
            # Look for valid frame header (0x44 for monitor mode)
            if self._buffer[0] != self.HEADER_MONITOR:
                # Skip until we find header
                try:
                    idx = self._buffer.index(self.HEADER_MONITOR)
                    self._buffer = self._buffer[idx:]
                except ValueError:
                    self._buffer.clear()
                    break
                continue

            frame_data = bytes(self._buffer[:16])
            self._buffer = self._buffer[16:]

            parsed = parse_frame(frame_data)
            if parsed:
                lin_frame = parsed.to_lin_frame()
                if lin_frame:
                    yield lin_frame

    def send_frame(self, frame: LinFrame) -> bool:
        """Send a LIN frame (not implemented for monitor mode)."""
        raise NotImplementedError("LINTest-MI in monitor mode cannot send frames")

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False
