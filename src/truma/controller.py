"""Truma heater controller - command encoding.

Builds LIN frames to control Truma Combi heaters.

Frame 0x20 - Heater Command (Master → Heater):
- Byte 0: Room temperature setpoint
- Byte 1: Control flags
- Byte 2: Water temperature level
- Byte 3: Fuel control
- Byte 4: Electric power level
- Byte 5: Ventilation + energy bitmap
- Byte 6: Constant (0xE0)
- Byte 7: Constant (0x0F)
"""

from dataclasses import dataclass
from enum import IntEnum
from typing import Optional

from lin.frame import LinFrame, ChecksumType


class WaterLevel(IntEnum):
    """Water heater temperature level."""
    OFF = 0
    ECO = 40    # ~40°C
    HOT = 60    # ~60°C


class EnergySource(IntEnum):
    """Energy source selection."""
    NONE = 0b00
    FUEL = 0b01      # Gas/Diesel
    ELECTRIC = 0b10
    MIX = 0b11       # Both


class VentLevel(IntEnum):
    """Ventilation level."""
    OFF = 0x0
    LEVEL_1 = 0x1
    LEVEL_2 = 0x2
    LEVEL_3 = 0x3
    LEVEL_4 = 0x4
    LEVEL_5 = 0x5
    LEVEL_6 = 0x6
    LEVEL_7 = 0x7
    LEVEL_8 = 0x8
    LEVEL_9 = 0x9
    LEVEL_10 = 0xA
    ECO = 0xB        # Eco/Comfort automatic
    HIGH = 0xD       # High automatic


@dataclass
class HeaterCommand:
    """Heater command settings."""
    # Room heating
    room_temp: Optional[int] = None      # 5-30°C, None=off

    # Water heating
    water_level: WaterLevel = WaterLevel.OFF

    # Energy sources
    energy_source: EnergySource = EnergySource.FUEL
    fuel_enabled: bool = True
    electric_power: int = 0              # Watts: 0, 900, 1800

    # Ventilation
    vent_level: VentLevel = VentLevel.OFF


class TrumaController:
    """Controller for building Truma heater commands."""

    FRAME_ID_COMMAND = 0x20

    @staticmethod
    def encode_room_temp(temp_celsius: Optional[int]) -> int:
        """Encode room temperature setpoint.

        Formula: code = (170 + (temp - 5) * 10) & 0xFF
        Special: 0xAA = heating off

        Args:
            temp_celsius: Target temperature 5-30°C, or None for off

        Returns:
            Encoded byte value
        """
        if temp_celsius is None or temp_celsius == 0:
            return 0xAA  # Off

        # Clamp to valid range
        temp = max(5, min(30, temp_celsius))

        # D4E formula (no +5 offset in our version)
        code = (170 + temp * 10) & 0xFF
        return code

    @staticmethod
    def decode_room_temp(code: int) -> Optional[int]:
        """Decode room temperature setpoint.

        Formula: temp = ((code - 170) mod 256) / 10
        """
        if code == 0xAA:
            return None

        if code < 170:
            code += 256
        temp = (code - 170) / 10.0

        if 5 <= temp <= 31:  # Allow 30°C (boundary)
            return int(temp)
        return None

    @staticmethod
    def encode_control_flags(room_heat_on: bool, water_hot: bool) -> int:
        """Encode control flags byte.

        Base value 0xAA with:
        - Bit 0: Room heating enable (1=on)
        - Bit 7: Water level inverted (0=hot, 1=off/eco)

        Args:
            room_heat_on: Enable room heating
            water_hot: True for hot (60°C), False for off/eco

        Returns:
            Encoded byte value
        """
        flags = 0xAA  # Base value

        if room_heat_on:
            flags |= 0x01  # Set bit 0

        if water_hot:
            flags &= ~0x80  # Clear bit 7 (inverted)
        else:
            flags |= 0x80  # Set bit 7

        return flags

    @staticmethod
    def encode_water_level(level: WaterLevel) -> int:
        """Encode water temperature level.

        Returns:
            0xAA=off, 0xC3=eco(40°C), 0xD0=hot(60°C)
        """
        if level == WaterLevel.OFF:
            return 0xAA
        elif level == WaterLevel.ECO:
            return 0xC3
        elif level == WaterLevel.HOT:
            return 0xD0
        return 0xAA

    @staticmethod
    def encode_fuel_control(enabled: bool) -> int:
        """Encode fuel (gas/diesel) control.

        Returns:
            0xFA=enabled, 0x00=disabled
        """
        return 0xFA if enabled else 0x00

    @staticmethod
    def encode_electric_power(watts: int) -> int:
        """Encode electric power level.

        Args:
            watts: Power in watts (0, 900, 1800)

        Returns:
            Encoded byte (power / 100)
        """
        # Clamp to valid values
        if watts <= 0:
            return 0x00
        elif watts <= 900:
            return 0x09  # 900W
        else:
            return 0x12  # 1800W

    @staticmethod
    def encode_vent_energy(vent_level: VentLevel, energy: EnergySource) -> int:
        """Encode ventilation level and energy source bitmap.

        Bits 0-1: Energy source
        Bits 4-7: Ventilation level

        Returns:
            Encoded byte
        """
        return (vent_level << 4) | (energy & 0x03)

    def build_command(self, cmd: HeaterCommand) -> bytes:
        """Build complete 8-byte command frame data.

        Args:
            cmd: HeaterCommand with desired settings

        Returns:
            8-byte command data
        """
        room_heat_on = cmd.room_temp is not None and cmd.room_temp > 0
        water_hot = cmd.water_level == WaterLevel.HOT

        data = bytes([
            self.encode_room_temp(cmd.room_temp),
            self.encode_control_flags(room_heat_on, water_hot),
            self.encode_water_level(cmd.water_level),
            self.encode_fuel_control(cmd.fuel_enabled),
            self.encode_electric_power(cmd.electric_power),
            self.encode_vent_energy(cmd.vent_level, cmd.energy_source),
            0xE0,  # Constant
            0x0F,  # Constant
        ])

        return data

    def build_frame(self, cmd: HeaterCommand) -> LinFrame:
        """Build complete LIN frame for command.

        Args:
            cmd: HeaterCommand with desired settings

        Returns:
            LinFrame ready to send
        """
        data = self.build_command(cmd)

        # Create frame with placeholder checksum, then calculate real one
        frame = LinFrame(
            frame_id=self.FRAME_ID_COMMAND,
            data=data,
            checksum=0,  # Placeholder
            checksum_type=ChecksumType.ENHANCED,
        )
        # Replace with calculated checksum
        return LinFrame(
            frame_id=self.FRAME_ID_COMMAND,
            data=data,
            checksum=frame.calculate_checksum(),
            checksum_type=ChecksumType.ENHANCED,
        )

    # Convenience methods for common commands

    def cmd_heating_off(self) -> LinFrame:
        """Build command to turn off room heating."""
        return self.build_frame(HeaterCommand(
            room_temp=None,
            water_level=WaterLevel.OFF,
            fuel_enabled=False,
            electric_power=0,
            vent_level=VentLevel.OFF,
        ))

    def cmd_set_room_temp(self, temp: int, energy: EnergySource = EnergySource.FUEL) -> LinFrame:
        """Build command to set room temperature.

        Args:
            temp: Target temperature 5-30°C
            energy: Energy source to use
        """
        return self.build_frame(HeaterCommand(
            room_temp=temp,
            energy_source=energy,
            fuel_enabled=(energy in [EnergySource.FUEL, EnergySource.MIX]),
            electric_power=1800 if energy in [EnergySource.ELECTRIC, EnergySource.MIX] else 0,
            vent_level=VentLevel.ECO,
        ))

    def cmd_set_water(self, level: WaterLevel, energy: EnergySource = EnergySource.FUEL) -> LinFrame:
        """Build command to set water heating level.

        Args:
            level: Water temperature level
            energy: Energy source to use
        """
        return self.build_frame(HeaterCommand(
            water_level=level,
            energy_source=energy,
            fuel_enabled=(energy in [EnergySource.FUEL, EnergySource.MIX]),
            electric_power=1800 if energy in [EnergySource.ELECTRIC, EnergySource.MIX] else 0,
        ))
