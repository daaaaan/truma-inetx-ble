"""Truma protocol decoder.

Decodes LIN frames from Truma Combi heating systems.

Frame ID mapping:
- 0x20: Status frame 1 - current room temperature
- 0x21: Status frame 2 - water/heating status
- 0x22: Status frame 3 - operating status
- 0x3C: Master request (transport layer)
- 0x3D: Slave response (transport layer)

Transport layer SIDs:
- 0xB2/0xF2: Read/response - contains settings
- 0xB8/0xF8: Heartbeat
"""

from dataclasses import dataclass
from typing import Optional
from enum import IntEnum


class HeatingMode(IntEnum):
    OFF = 0
    ECO = 1
    HIGH = 2


class EnergyMix(IntEnum):
    NONE = 0
    GAS = 1      # Diesel/Gas
    ELECTRIC = 2
    MIX = 3      # Hybrid


class WaterMode(IntEnum):
    OFF = 0
    ECO = 40
    HOT = 60
    BOOST = 200


@dataclass
class TrumaStatus:
    """Decoded Truma system status."""
    # Temperatures
    current_room_temp: Optional[float] = None      # °C
    target_room_temp: Optional[int] = None         # °C (0=off, 5-30)
    current_water_temp: Optional[float] = None     # °C
    target_water_temp: Optional[int] = None        # Raw value (0/40/60/200)

    # Operating modes
    heating_mode: Optional[HeatingMode] = None
    energy_mix: Optional[EnergyMix] = None
    electric_power: int = 0                        # Watts (0, 900, 1800)
    diesel_active: bool = False

    # Status
    operating: bool = False
    error_code: int = 0

    def water_mode_str(self) -> str:
        """Get water mode as string."""
        if self.target_water_temp is None:
            return "unknown"
        if self.target_water_temp == 0:
            return "off"
        elif self.target_water_temp == 40:
            return "eco"
        elif self.target_water_temp == 60:
            return "hot"
        elif self.target_water_temp == 200:
            return "boost"
        return f"unknown({self.target_water_temp})"


class TrumaDecoder:
    """Decoder for Truma LIN protocol."""

    def __init__(self):
        self.status = TrumaStatus()
        self._frame_buffer = {}

    def decode_frame(self, frame_id: int, data: bytes) -> Optional[str]:
        """Decode a LIN frame and update status.

        Returns a description string if something interesting was decoded.
        """
        if len(data) < 2:
            return None

        if frame_id == 0x20:
            return self._decode_status_1(data)
        elif frame_id == 0x21:
            return self._decode_status_2(data)
        elif frame_id == 0x22:
            return self._decode_status_3(data)
        elif frame_id == 0x3D:
            return self._decode_transport_response(data)

        return None

    def _decode_status_1(self, data: bytes) -> Optional[str]:
        """Decode status frame 1 (0x20).

        Byte 0-1: Header/counter (changes frequently)
        Byte 2: Unknown
        Byte 3: Diesel flag (250=on, 0=off)
        Byte 4: Electric power level (0=off, 9=900W, 18=1800W)
        Byte 5: Operating status (~210=running, ~2=off)
        Byte 6: Mode flags (240=on, 224=off)
        Byte 7: Unknown (0x0F)
        """
        if len(data) < 7:
            return None

        # Energy source decoding
        diesel_on = data[3] == 250
        electric_power = data[4] * 100  # 0, 900, or 1800

        self.status.diesel_active = diesel_on
        self.status.electric_power = electric_power

        # Determine energy mix
        if diesel_on and electric_power > 0:
            self.status.energy_mix = EnergyMix.MIX
        elif diesel_on:
            self.status.energy_mix = EnergyMix.GAS
        elif electric_power > 0:
            self.status.energy_mix = EnergyMix.ELECTRIC
        else:
            self.status.energy_mix = EnergyMix.NONE

        # Operating status
        op_byte = data[5]
        was_operating = self.status.operating
        self.status.operating = op_byte > 100  # ~210 when on, ~2 when off

        # Build status message
        msgs = []
        if self.status.operating != was_operating:
            state = "ON" if self.status.operating else "OFF"
            msgs.append(f"Heater {state}")

        if electric_power > 0:
            msgs.append(f"Electric: {electric_power}W")
        if diesel_on:
            msgs.append("Diesel: ON")

        return " | ".join(msgs) if msgs else None

    def _decode_status_2(self, data: bytes) -> Optional[str]:
        """Decode status frame 2 (0x21).

        Byte 0: Counter/sequence (changes frequently)
        Byte 1: Unknown
        Byte 2: Current room temperature (0.1°C units, single byte)
        Byte 3-7: Unknown status bytes
        """
        if len(data) < 3:
            return None

        # Byte 2 contains current room temp in 0.1°C units
        temp_raw = data[2]
        if 50 < temp_raw < 400:  # Sanity check (5-40°C range)
            self.status.current_room_temp = temp_raw / 10.0
            return f"Room temp: {self.status.current_room_temp:.1f}°C"
        return None

    def _decode_status_3(self, data: bytes) -> Optional[str]:
        """Decode status frame 3 (0x22).

        Contains operating status flags.
        """
        if len(data) < 4:
            return None

        # Byte 3 appears to contain status flags
        status_byte = data[3]
        self.status.operating = (status_byte & 0x04) != 0

        return None

    def _decode_transport_response(self, data: bytes) -> Optional[str]:
        """Decode transport layer response (0x3D).

        SID 0xF2 response format:
        - Byte 3: Target room temp (°C, 0=off)
        - Byte 4: Target water temp / mode
        - Byte 5: Operating mode flags
        - Byte 6: Energy mix
        """
        if len(data) < 7:
            return None

        nad, pci, sid = data[0], data[1], data[2]

        if sid == 0xF2:  # Response to read request
            payload = data[3:]

            # Only decode if this looks like settings data
            if len(payload) >= 4 and payload[0] > 0:
                target_room = payload[0]
                if 5 <= target_room <= 30:
                    self.status.target_room_temp = target_room

                # Byte 1 might be water temp setting
                water_raw = payload[1]
                if water_raw in [0, 40, 60, 200]:
                    self.status.target_water_temp = water_raw

                # Byte 3 might be energy mix
                if len(payload) > 3:
                    mix_raw = payload[3]
                    if mix_raw in [0, 1, 2, 3]:
                        self.status.energy_mix = EnergyMix(mix_raw)

                return f"Settings: room={target_room}°C, water={self.status.water_mode_str()}"

        return None

    def get_summary(self) -> str:
        """Get current status summary."""
        parts = []

        if self.status.current_room_temp is not None:
            parts.append(f"Room: {self.status.current_room_temp:.1f}°C")

        if self.status.target_room_temp is not None:
            parts.append(f"Target: {self.status.target_room_temp}°C")

        if self.status.target_water_temp is not None:
            parts.append(f"Water: {self.status.water_mode_str()}")

        if self.status.energy_mix is not None:
            parts.append(f"Energy: {self.status.energy_mix.name}")

        return " | ".join(parts) if parts else "No data"
