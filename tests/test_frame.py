"""Tests for LIN frame parsing and building."""

import pytest

from src.lin.frame import LinFrame, ChecksumType


class TestLinFramePID:
    """Test Protected ID calculation and verification."""

    def test_calculate_pid_id_0(self):
        """ID 0x00 should have PID 0x80 (P0=0, P1=1)."""
        assert LinFrame._calculate_pid(0x00) == 0x80

    def test_calculate_pid_id_1(self):
        """ID 0x01 should have PID 0x01 | parity bits."""
        pid = LinFrame._calculate_pid(0x01)
        # P0 = 1^0^0^0 = 1, P1 = ~(0^0^0^0) = 1
        assert pid == 0xC1

    def test_calculate_pid_id_3F(self):
        """ID 0x3F (max) should have correct PID."""
        pid = LinFrame._calculate_pid(0x3F)
        assert LinFrame.extract_id_from_pid(pid) == 0x3F
        assert LinFrame.verify_pid_parity(pid)

    def test_extract_id_from_pid(self):
        """Should extract 6-bit ID from PID."""
        for frame_id in [0x00, 0x12, 0x3C, 0x3F]:
            pid = LinFrame._calculate_pid(frame_id)
            assert LinFrame.extract_id_from_pid(pid) == frame_id

    def test_verify_pid_parity_valid(self):
        """Valid PIDs should verify."""
        for frame_id in range(64):
            pid = LinFrame._calculate_pid(frame_id)
            assert LinFrame.verify_pid_parity(pid)

    def test_verify_pid_parity_invalid(self):
        """Invalid PIDs should not verify."""
        # Flip a parity bit
        valid_pid = LinFrame._calculate_pid(0x12)
        invalid_pid = valid_pid ^ 0x40  # Flip P0
        assert not LinFrame.verify_pid_parity(invalid_pid)


class TestLinFrameChecksum:
    """Test checksum calculation."""

    def test_classic_checksum_simple(self):
        """Classic checksum should sum data bytes only."""
        frame = LinFrame(
            frame_id=0x12,
            data=bytes([0x01, 0x02, 0x03]),
            checksum=0x00,  # Will calculate
            checksum_type=ChecksumType.CLASSIC,
        )
        # Sum = 0x06, inverted = 0xF9
        expected = (~(0x01 + 0x02 + 0x03)) & 0xFF
        assert frame.calculate_checksum() == expected

    def test_enhanced_checksum_includes_pid(self):
        """Enhanced checksum should include PID."""
        frame = LinFrame(
            frame_id=0x12,
            data=bytes([0x01, 0x02, 0x03]),
            checksum=0x00,
            checksum_type=ChecksumType.ENHANCED,
        )
        # Should be different from classic
        classic_frame = LinFrame(
            frame_id=0x12,
            data=bytes([0x01, 0x02, 0x03]),
            checksum=0x00,
            checksum_type=ChecksumType.CLASSIC,
        )
        assert frame.calculate_checksum() != classic_frame.calculate_checksum()

    def test_checksum_with_carry(self):
        """Checksum should handle carry correctly."""
        # Use values that will cause carry
        frame = LinFrame(
            frame_id=0x00,
            data=bytes([0xFF, 0xFF]),
            checksum=0x00,
            checksum_type=ChecksumType.CLASSIC,
        )
        # 0xFF + 0xFF = 0x1FE -> 0xFE + 1 = 0xFF -> ~0xFF = 0x00
        assert frame.calculate_checksum() == 0x00

    def test_verify_checksum_valid(self):
        """Valid checksum should verify."""
        frame = LinFrame(
            frame_id=0x12,
            data=bytes([0x01, 0x02, 0x03]),
            checksum=0x00,
            checksum_type=ChecksumType.CLASSIC,
        )
        frame.checksum = frame.calculate_checksum()
        assert frame.verify_checksum()

    def test_verify_checksum_invalid(self):
        """Invalid checksum should not verify."""
        frame = LinFrame(
            frame_id=0x12,
            data=bytes([0x01, 0x02, 0x03]),
            checksum=0xAB,  # Wrong
            checksum_type=ChecksumType.CLASSIC,
        )
        assert not frame.verify_checksum()


class TestLinFrameSerialization:
    """Test frame to/from bytes conversion."""

    def test_to_bytes(self):
        """Frame should serialize correctly."""
        frame = LinFrame(
            frame_id=0x12,
            data=bytes([0x01, 0x02, 0x03]),
            checksum=0xAB,
        )
        raw = frame.to_bytes()

        assert raw[0] == 0x55  # Sync
        assert raw[1] == frame.pid  # PID
        assert raw[2:5] == bytes([0x01, 0x02, 0x03])  # Data
        assert raw[5] == 0xAB  # Checksum

    def test_from_bytes_valid(self):
        """Should parse valid frame bytes."""
        # Build a valid frame
        original = LinFrame(
            frame_id=0x12,
            data=bytes([0x01, 0x02, 0x03, 0x04]),
            checksum=0x00,
            checksum_type=ChecksumType.ENHANCED,
        )
        original.checksum = original.calculate_checksum()

        raw = original.to_bytes()
        parsed = LinFrame.from_bytes(raw, checksum_type=ChecksumType.ENHANCED)

        assert parsed.frame_id == original.frame_id
        assert parsed.data == original.data
        assert parsed.checksum == original.checksum

    def test_from_bytes_invalid_sync(self):
        """Should reject invalid sync byte."""
        raw = bytes([0x00, 0x12, 0x01, 0x02, 0x03, 0xAB])  # Wrong sync
        with pytest.raises(ValueError, match="Invalid sync byte"):
            LinFrame.from_bytes(raw)

    def test_from_bytes_invalid_pid_parity(self):
        """Should reject invalid PID parity."""
        raw = bytes([0x55, 0x12, 0x01, 0x02, 0x03, 0xAB])  # Bad PID parity
        with pytest.raises(ValueError, match="Invalid PID parity"):
            LinFrame.from_bytes(raw)

    def test_from_bytes_invalid_checksum(self):
        """Should reject invalid checksum."""
        # Build frame with wrong checksum
        original = LinFrame(
            frame_id=0x12,
            data=bytes([0x01, 0x02]),
            checksum=0x00,
            checksum_type=ChecksumType.ENHANCED,
        )
        raw = bytes([0x55, original.pid, 0x01, 0x02, 0xFF])  # Wrong checksum

        with pytest.raises(ValueError, match="Invalid checksum"):
            LinFrame.from_bytes(raw, checksum_type=ChecksumType.ENHANCED)

    def test_from_bytes_skip_validation(self):
        """Should parse without validation when requested."""
        raw = bytes([0x55, 0x12, 0x01, 0x02, 0xFF])  # Invalid PID and checksum
        frame = LinFrame.from_bytes(raw, validate=False)

        assert frame.frame_id == 0x12 & 0x3F
        assert frame.data == bytes([0x01, 0x02])
        assert frame.checksum == 0xFF


class TestLinFrameValidation:
    """Test frame validation."""

    def test_frame_id_range(self):
        """Frame ID must be 0-63."""
        with pytest.raises(ValueError, match="frame_id must be 0-63"):
            LinFrame(frame_id=64, data=b"", checksum=0)

        with pytest.raises(ValueError, match="frame_id must be 0-63"):
            LinFrame(frame_id=-1, data=b"", checksum=0)

    def test_data_max_length(self):
        """Data must be 0-8 bytes."""
        with pytest.raises(ValueError, match="data must be 0-8 bytes"):
            LinFrame(frame_id=0, data=bytes(9), checksum=0)

    def test_valid_frame_creation(self):
        """Valid frames should create without error."""
        frame = LinFrame(frame_id=0x3F, data=bytes(8), checksum=0xFF)
        assert frame.frame_id == 0x3F
        assert len(frame.data) == 8


class TestLinFrameDisplay:
    """Test string representations."""

    def test_str_format(self):
        """String representation should be readable."""
        frame = LinFrame(
            frame_id=0x12,
            data=bytes([0x01, 0xAB]),
            checksum=0x00,
            checksum_type=ChecksumType.CLASSIC,
        )
        frame.checksum = frame.calculate_checksum()

        s = str(frame)
        assert "0x12" in s.upper() or "0X12" in s.upper()
        assert "01" in s.upper()
        assert "AB" in s.upper()
        assert "ok" in s.lower()

    def test_repr_format(self):
        """Repr should show all fields."""
        frame = LinFrame(
            frame_id=0x12,
            data=bytes([0x01]),
            checksum=0xAB,
            checksum_type=ChecksumType.ENHANCED,
        )
        r = repr(frame)
        assert "LinFrame" in r
        assert "0x12" in r.lower()
        assert "ENHANCED" in r
