"""D-Bus service exposing Truma heater state to Venus OS."""
import sys
import os
import logging

# Add velib_python to path
sys.path.insert(0, '/opt/victronenergy/dbus-tempsensor-relay/ext/velib_python')

try:
    from vedbus import VeDbusService
    HAS_VEDBUS = True
except ImportError:
    HAS_VEDBUS = False
    print("[DBUS] velib_python not available — D-Bus service disabled")

logger = logging.getLogger("truma.dbus")


class TrumaDbusService:
    """Expose Truma heater data on Venus OS D-Bus."""

    SERVICE_NAME = "com.victronenergy.temperature.truma"

    def __init__(self, send_command_callback=None):
        """Initialize D-Bus service.

        Args:
            send_command_callback: async callable(topic, param, value) for sending commands
        """
        self._send_command = send_command_callback
        self._service = None

        if not HAS_VEDBUS:
            logger.warning("VeDbusService not available")
            return

        self._service = VeDbusService(
            self.SERVICE_NAME,
            register=False  # don't register yet
        )

        # Device info
        self._service.add_path('/DeviceInstance', 100)
        self._service.add_path('/ProductId', 0)
        self._service.add_path('/ProductName', 'Truma Heater')
        self._service.add_path('/FirmwareVersion', '1.0')
        self._service.add_path('/Connected', 0)

        # Room climate
        self._service.add_path('/Truma/RoomClimate/Mode', None, writeable=True,
                               onchangecallback=self._on_write)
        self._service.add_path('/Truma/RoomClimate/TargetTemp', None, writeable=True,
                               onchangecallback=self._on_write)
        self._service.add_path('/Truma/RoomClimate/CurrentTemp', None)
        self._service.add_path('/Truma/RoomClimate/Active', None)

        # Air heating
        self._service.add_path('/Truma/AirHeating/TargetTemp', None, writeable=True,
                               onchangecallback=self._on_write)
        self._service.add_path('/Truma/AirHeating/CurrentTemp', None)
        self._service.add_path('/Truma/AirHeating/Mode', None, writeable=True,
                               onchangecallback=self._on_write)
        self._service.add_path('/Truma/AirHeating/Active', None)
        self._service.add_path('/Truma/AirHeating/FanLevel', None, writeable=True,
                               onchangecallback=self._on_write)

        # Water heating
        self._service.add_path('/Truma/WaterHeating/Mode', None, writeable=True,
                               onchangecallback=self._on_write)
        self._service.add_path('/Truma/WaterHeating/Active', None, writeable=True,
                               onchangecallback=self._on_write)
        self._service.add_path('/Truma/WaterHeating/CurrentTemp', None)

        # Energy
        self._service.add_path('/Truma/Energy/DieselLevel', None, writeable=True,
                               onchangecallback=self._on_write)
        self._service.add_path('/Truma/Energy/ElectricLevel', None, writeable=True,
                               onchangecallback=self._on_write)

        # System
        self._service.add_path('/Truma/System/FlameStatus', None)
        self._service.add_path('/Truma/System/Voltage', None)
        self._service.add_path('/Truma/System/InternalTemp', None)

        # Register the service
        self._service.register()
        logger.info("D-Bus service registered: %s", self.SERVICE_NAME)

    def _on_write(self, path, value):
        """Handle D-Bus write from HA/MQTT."""
        if self._send_command is None:
            return False

        # Map D-Bus path back to topic/param
        path_map = {
            '/Truma/RoomClimate/Mode': ('RoomClimate', 'Mode'),
            '/Truma/RoomClimate/TargetTemp': ('RoomClimate', 'TgtTemp'),
            '/Truma/AirHeating/TargetTemp': ('AirHeating', 'TgtTemp'),
            '/Truma/AirHeating/Mode': ('AirHeating', 'Mode'),
            '/Truma/AirHeating/FanLevel': ('AirCirculation', 'FanLevel'),
            '/Truma/WaterHeating/Mode': ('WaterHeating', 'Mode'),
            '/Truma/WaterHeating/Active': ('WaterHeating', 'Active'),
            '/Truma/Energy/DieselLevel': ('EnergySrc', 'DieselLevel'),
            '/Truma/Energy/ElectricLevel': ('EnergySrc', 'ElectricLevel'),
        }

        mapping = path_map.get(path)
        if mapping:
            topic, param = mapping
            # Temperature paths need conversion from Celsius to wire (tenths)
            wire_value = value
            if 'Temp' in path and isinstance(value, (int, float)):
                wire_value = int(value * 10)

            logger.info("D-Bus write: %s = %s -> %s.%s = %s", path, value, topic, param, wire_value)
            # Fire-and-forget the command (callback handles async)
            self._send_command(topic, param, int(wire_value))
            return True

        return False

    def update_from_state(self, state):
        """Push state changes to D-Bus paths.

        Args:
            state: TrumaState instance
        """
        if not self._service:
            return

        def _set(path, value):
            try:
                self._service[path] = value
            except Exception:
                pass

        # Connection status
        _set('/Connected', 1 if state.connected else 0)

        # Room climate (temperatures converted from wire to Celsius)
        _set('/Truma/RoomClimate/Mode', state.room_mode)
        _set('/Truma/RoomClimate/TargetTemp',
             state.wire_to_celsius(state.room_target_temp))
        _set('/Truma/RoomClimate/CurrentTemp',
             state.wire_to_celsius(state.air_current_temp))
        _set('/Truma/RoomClimate/Active', state.room_active)

        # Air heating
        _set('/Truma/AirHeating/TargetTemp',
             state.wire_to_celsius(state.air_target_temp))
        _set('/Truma/AirHeating/CurrentTemp',
             state.wire_to_celsius(state.air_current_temp))
        _set('/Truma/AirHeating/Mode', state.air_mode)
        _set('/Truma/AirHeating/Active', state.air_active)
        _set('/Truma/AirHeating/FanLevel', state.fan_level)

        # Water heating
        _set('/Truma/WaterHeating/Mode', state.water_mode)
        _set('/Truma/WaterHeating/Active', state.water_active)
        _set('/Truma/WaterHeating/CurrentTemp',
             state.wire_to_celsius(state.water_current_temp))

        # Energy
        _set('/Truma/Energy/DieselLevel', state.diesel_level)
        _set('/Truma/Energy/ElectricLevel', state.electric_level)

        # System
        _set('/Truma/System/FlameStatus', state.flame_status)
        _set('/Truma/System/Voltage',
             (state.voltage_vcc12 / 1000.0) if state.voltage_vcc12 else None)
        _set('/Truma/System/InternalTemp',
             state.wire_to_celsius(state.internal_temp))
