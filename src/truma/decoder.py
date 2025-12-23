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
    """Water heater mode.

    Decoded from 0x22 byte2 and 0x21 byte5:
    - OFF: byte2=16, byte5=49 (heater inactive)
    - ECO: byte2=16, byte5=1 (heater active, low temp)
    - COMFORT: byte2=17, byte5=1
    - HOT: byte2=49, byte5=1
    """
    OFF = 0
    ECO = 1
    COMFORT = 2
    HOT = 3


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
    water_mode: Optional[WaterMode] = None
    water_heater_active: bool = False

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
        """Decode frame 0x20 - Heater Command.

        Protocol 4.0 / D4E format (from wiki.womonet.io):
        Byte 0: Room setpoint - temp = ((code - 170) mod 256) / 10, 0xAA=OFF
        Byte 1: Control flags (bit0=heating, bit7=water mode inv)
        Byte 2: Water setpoint (0xAA=OFF, 0xC3=ECO/40°C, 0xD0=HOT/60°C)
        Byte 3: Fuel control (0xFA=enabled, 0x00=disabled)
        Byte 4: Electric power level (×100W: 0=off, 9=900W, 18=1800W)
        Byte 5: Ventilation (bits 4-7: level)
        Byte 6: Constant (0xE0)
        Byte 7: Unknown (0x0F)
        """
        if len(data) < 7:
            return None

        msgs = []

        # Byte 0: Room temperature setpoint
        room_code = data[0]
        if room_code == 0xAA:
            self.status.target_room_temp = 0  # OFF
        else:
            # D4E formula: temp = ((code - 170) mod 256) / 10
            if room_code < 170:
                room_code += 256
            target_room = (room_code - 170) / 10.0
            if 5 <= target_room <= 30:
                self.status.target_room_temp = int(target_room)

        # Byte 2: Water temperature setpoint
        water_code = data[2]
        if water_code == 0xAA:
            self.status.target_water_temp = 0
        elif water_code == 0xC3:
            self.status.target_water_temp = 40  # ECO
        elif water_code == 0xD0:
            self.status.target_water_temp = 60  # HOT
        else:
            self.status.target_water_temp = water_code

        # Byte 3: Fuel/diesel control
        diesel_on = data[3] == 0xFA
        self.status.diesel_active = diesel_on

        # Byte 4: Electric power
        electric_power = data[4] * 100
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

        # Byte 5: Ventilation level (bits 4-7)
        vent_level = (data[5] >> 4) & 0x0F
        # 0xB = Eco, 0xD = High
        was_operating = self.status.operating
        self.status.operating = vent_level > 0

        # Build status message
        if self.status.operating != was_operating:
            state = "ON" if self.status.operating else "OFF"
            msgs.append(f"Heater {state}")

        if electric_power > 0:
            msgs.append(f"Electric: {electric_power}W")
        if diesel_on:
            msgs.append("Diesel: ON")

        return " | ".join(msgs) if msgs else None

    def _decode_status_2(self, data: bytes) -> Optional[str]:
        """Decode status frame 2 (0x21) - Heater Info 1.

        Protocol 4.0 / D4E format (from wiki.womonet.io):
        Bytes 0-2: Two 12-bit temperatures packed in Kelvin×10
        Byte 3: Burner power (×100W)
        Byte 4: Electric power (×100W)
        Byte 5: Status/energy/fan (bits 0-1: energy, bits 4-6: fan)
        Byte 6-7: Unknown (usually 0xF0 0x0F)
        """
        if len(data) < 6:
            return None

        msgs = []

        # Bytes 0-2: 12-bit packed temperatures in Kelvin×10
        byte0, byte1, byte2 = data[0], data[1], data[2]

        # Room temp: ((byte1 & 0x0F) << 8) | byte0
        room_raw = ((byte1 & 0x0F) << 8) | byte0
        room_celsius = room_raw / 10.0 - 273.0
        if 0 < room_celsius < 50:  # Sanity check
            self.status.current_room_temp = room_celsius
            msgs.append(f"Room: {room_celsius:.1f}°C")

        # Water temp: (byte2 << 4) | (byte1 >> 4)
        water_raw = (byte2 << 4) | (byte1 >> 4)
        water_celsius = water_raw / 10.0 - 273.0
        if 0 < water_celsius < 100:  # Sanity check
            self.status.current_water_temp = water_celsius

        # Byte 5: status/energy/fan
        status_byte = data[5]
        # Bits 0-1: energy source active
        # Bits 4-6: fan speed
        water_active = (status_byte & 0x03) != 0
        if water_active != self.status.water_heater_active:
            self.status.water_heater_active = water_active
            state = "ON" if water_active else "OFF"
            msgs.append(f"Water heater: {state}")

        return " | ".join(msgs) if msgs else None

    def _decode_status_3(self, data: bytes) -> Optional[str]:
        """Decode status frame 3 (0x22).

        Byte 0: Counter/status
        Byte 1: Unknown (often 240 or 112)
        Byte 2: Water mode (16=ECO/OFF, 17=COMFORT, 49=HOT)
        Byte 3: Status flags
        Byte 4-7: 0xFF padding
        """
        if len(data) < 4:
            return None

        # Byte 2: water mode
        water_byte = data[2]
        old_mode = self.status.water_mode

        if water_byte == 49:
            self.status.water_mode = WaterMode.HOT
        elif water_byte == 17:
            self.status.water_mode = WaterMode.COMFORT
        elif water_byte == 16:
            # Could be ECO or OFF - check water_heater_active from 0x21
            if self.status.water_heater_active:
                self.status.water_mode = WaterMode.ECO
            else:
                self.status.water_mode = WaterMode.OFF

        if self.status.water_mode != old_mode and self.status.water_mode is not None:
            return f"Water mode: {self.status.water_mode.name}"

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
