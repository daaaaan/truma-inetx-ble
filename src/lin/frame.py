"""LIN (Local Interconnect Network) frame parsing and building."""

from dataclasses import dataclass
from enum import Enum


class ChecksumType(Enum):
    """LIN checksum calculation method."""

    CLASSIC = "classic"  # Checksum over data bytes only (LIN 1.x)
    ENHANCED = "enhanced"  # Checksum over PID + data bytes (LIN 2.x)


@dataclass
class LinFrame:
    """Represents a LIN bus frame.

    Attributes:
        frame_id: 6-bit frame identifier (0-63)
        data: Payload bytes (0-8 bytes)
        checksum: Frame checksum byte
        checksum_type: Classic or Enhanced checksum
    """

    frame_id: int
    data: bytes
    checksum: int
    checksum_type: ChecksumType = ChecksumType.ENHANCED

    SYNC_BYTE = 0x55

    def __post_init__(self) -> None:
        if not 0 <= self.frame_id <= 63:
            raise ValueError(f"frame_id must be 0-63, got {self.frame_id}")
        if len(self.data) > 8:
            raise ValueError(f"data must be 0-8 bytes, got {len(self.data)}")

    @property
    def pid(self) -> int:
        """Protected Identifier: 6-bit ID + 2 parity bits."""
        return self._calculate_pid(self.frame_id)

    @staticmethod
    def _calculate_pid(frame_id: int) -> int:
        """Calculate Protected ID from 6-bit frame ID.

        P0 = ID0 ^ ID1 ^ ID2 ^ ID4
        P1 = !(ID1 ^ ID3 ^ ID4 ^ ID5)
        """
        id0 = (frame_id >> 0) & 1
        id1 = (frame_id >> 1) & 1
        id2 = (frame_id >> 2) & 1
        id3 = (frame_id >> 3) & 1
        id4 = (frame_id >> 4) & 1
        id5 = (frame_id >> 5) & 1

        p0 = id0 ^ id1 ^ id2 ^ id4
        p1 = 1 - (id1 ^ id3 ^ id4 ^ id5)  # Inverted

        return frame_id | (p0 << 6) | (p1 << 7)

    @staticmethod
    def extract_id_from_pid(pid: int) -> int:
        """Extract 6-bit frame ID from Protected ID."""
        return pid & 0x3F

    @staticmethod
    def verify_pid_parity(pid: int) -> bool:
        """Verify the parity bits in a Protected ID."""
        frame_id = pid & 0x3F
        expected_pid = LinFrame._calculate_pid(frame_id)
        return pid == expected_pid

    def calculate_checksum(self) -> int:
        """Calculate the checksum for this frame."""
        if self.checksum_type == ChecksumType.ENHANCED:
            # Enhanced: PID + data
            data_for_checksum = bytes([self.pid]) + self.data
        else:
            # Classic: data only
            data_for_checksum = self.data

        return self._compute_checksum(data_for_checksum)

    @staticmethod
    def _compute_checksum(data: bytes) -> int:
        """Compute LIN checksum with carry handling.

        Sum all bytes, add carry bits back, then invert.
        """
        checksum = 0
        for byte in data:
            checksum += byte
            if checksum > 255:
                checksum = (checksum & 0xFF) + 1  # Add carry

        return (~checksum) & 0xFF

    def verify_checksum(self) -> bool:
        """Verify the frame's checksum is correct."""
        return self.checksum == self.calculate_checksum()

    def to_bytes(self) -> bytes:
        """Serialize frame to bytes (SYNC + PID + DATA + CHECKSUM)."""
        return bytes([self.SYNC_BYTE, self.pid]) + self.data + bytes([self.checksum])

    @classmethod
    def from_bytes(
        cls,
        raw: bytes,
        checksum_type: ChecksumType = ChecksumType.ENHANCED,
        validate: bool = True,
    ) -> "LinFrame":
        """Parse a LIN frame from raw bytes.

        Expected format: [SYNC] [PID] [DATA...] [CHECKSUM]

        Args:
            raw: Raw bytes including sync, PID, data, and checksum
            checksum_type: Checksum calculation method
            validate: If True, verify PID parity and checksum

        Returns:
            Parsed LinFrame

        Raises:
            ValueError: If frame is invalid or too short
        """
        if len(raw) < 3:
            raise ValueError(f"Frame too short: {len(raw)} bytes (min 3)")

        sync = raw[0]
        if sync != cls.SYNC_BYTE:
            raise ValueError(f"Invalid sync byte: 0x{sync:02X} (expected 0x55)")

        pid = raw[1]
        if validate and not cls.verify_pid_parity(pid):
            raise ValueError(f"Invalid PID parity: 0x{pid:02X}")

        frame_id = cls.extract_id_from_pid(pid)
        data = raw[2:-1]
        checksum = raw[-1]

        if len(data) > 8:
            raise ValueError(f"Data too long: {len(data)} bytes (max 8)")

        frame = cls(
            frame_id=frame_id,
            data=data,
            checksum=checksum,
            checksum_type=checksum_type,
        )

        if validate and not frame.verify_checksum():
            expected = frame.calculate_checksum()
            raise ValueError(
                f"Invalid checksum: 0x{checksum:02X} (expected 0x{expected:02X})"
            )

        return frame

    def __str__(self) -> str:
        """Human-readable representation."""
        data_hex = " ".join(f"{b:02X}" for b in self.data)
        checksum_ok = "ok" if self.verify_checksum() else "BAD"
        return f"ID=0x{self.frame_id:02X} [{data_hex}] CHK=0x{self.checksum:02X} ({checksum_ok})"

    def __repr__(self) -> str:
        return (
            f"LinFrame(frame_id=0x{self.frame_id:02X}, "
            f"data={self.data!r}, "
            f"checksum=0x{self.checksum:02X}, "
            f"checksum_type={self.checksum_type})"
        )
