"""MQTT client with Home Assistant auto-discovery."""
import json
import time
import threading
import logging
from typing import Callable, Optional

try:
    import paho.mqtt.client as mqtt
    HAS_MQTT = True
except ImportError:
    HAS_MQTT = False

log = logging.getLogger("truma.mqtt")

MQTT_HOST = "192.168.1.55"
MQTT_PORT = 1883
DEVICE_INFO = {
    "identifiers": ["truma_heater"],
    "name": "Truma Combi D 4 E",
    "manufacturer": "Truma",
    "model": "Combi D 4 E GEN2",
    "sw_version": "1.0"
}


class TrumaMqtt:
    def __init__(self, state_getter, command_sender):
        """
        Args:
            state_getter: callable() -> dict (from TrumaState.get_status)
            command_sender: callable(topic, param, value) -> (bool, str)
        """
        self._state_getter = state_getter
        self._command_sender = command_sender
        self._client = None
        self._thread = None
        self._running = False

    def start(self):
        if not HAS_MQTT:
            log.warning("paho-mqtt not available")
            return
        self._running = True
        try:
            self._client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="truma-service")
        except (AttributeError, TypeError):
            self._client = mqtt.Client(client_id="truma-service")
        self._client.on_connect = self._on_connect
        self._client.on_message = self._on_message
        self._client.will_set("truma/status", "offline", retain=True)
        try:
            self._client.connect(MQTT_HOST, MQTT_PORT, 60)
        except Exception as e:
            log.error("MQTT connect failed: %s", e)
            return
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        log.info("MQTT started")

    def _run(self):
        self._client.loop_start()
        while self._running:
            self._publish_state()
            time.sleep(5)
        self._client.loop_stop()

    def _on_connect(self, client, userdata, flags, rc, *args):
        log.info("MQTT connected (rc=%s)", rc)
        self._publish_discovery()
        client.publish("truma/status", "online", retain=True)
        # Subscribe to command topics
        client.subscribe("truma/room/mode/set")
        client.subscribe("truma/room/temp/set")
        client.subscribe("truma/water/mode/set")
        client.subscribe("truma/water/active/set")
        client.subscribe("truma/energy/diesel/set")
        client.subscribe("truma/energy/electric/set")
        client.subscribe("truma/fan/level/set")

    def _on_message(self, client, userdata, msg):
        """Handle commands from HA."""
        topic = msg.topic
        payload = msg.payload.decode()
        log.info("MQTT cmd: %s = %s", topic, payload)

        try:
            if topic == "truma/room/mode/set":
                mode_map = {"off": 0, "heat": 3, "fan_only": 5}
                value = mode_map.get(payload, 0)
                self._command_sender("RoomClimate", "Mode", value)
            elif topic == "truma/room/temp/set":
                # HA sends Celsius float, convert to wire (tenths)
                wire = int(float(payload) * 10)
                self._command_sender("RoomClimate", "TgtTemp", wire)
            elif topic == "truma/water/mode/set":
                mode_map = {"off": -1, "40": 0, "60": 1, "70": 2}
                value = mode_map.get(payload, -1)
                if value == -1:
                    self._command_sender("WaterHeating", "Active", 0)
                else:
                    self._command_sender("WaterHeating", "Mode", value)
                    self._command_sender("WaterHeating", "Active", 1)
            elif topic == "truma/water/active/set":
                self._command_sender("WaterHeating", "Active", int(payload))
            elif topic == "truma/energy/diesel/set":
                self._command_sender("EnergySrc", "DieselLevel", int(payload))
            elif topic == "truma/energy/electric/set":
                elec_map = {"Off": 0, "900W": 1, "1800W": 2, "0": 0, "1": 1, "2": 2}
                self._command_sender("EnergySrc", "ElectricLevel", elec_map.get(payload, 0))
            elif topic == "truma/fan/level/set":
                self._command_sender("AirCirculation", "FanLevel", int(float(payload)))
        except Exception as e:
            log.error("MQTT command error: %s", e)

    def _publish_discovery(self):
        """Publish HA MQTT Discovery config messages."""
        # Climate entity for room heating
        self._publish_config("climate", "room", {
            "name": "Room Heating",
            "unique_id": "truma_room_heating",
            "modes": ["off", "heat", "fan_only"],
            "mode_command_topic": "truma/room/mode/set",
            "mode_state_topic": "truma/room/mode/state",
            "temperature_command_topic": "truma/room/temp/set",
            "temperature_state_topic": "truma/room/temp/state",
            "current_temperature_topic": "truma/room/current_temp",
            "temperature_unit": "C",
            "min_temp": 16, "max_temp": 30, "temp_step": 0.5,
            "availability_topic": "truma/status",
            "device": DEVICE_INFO,
        })

        # Water heater — use select entity for mode since HA water_heater is limited
        self._publish_config("select", "water_mode", {
            "name": "Water Heating Mode",
            "unique_id": "truma_water_mode",
            "command_topic": "truma/water/mode/set",
            "state_topic": "truma/water/mode/state",
            "options": ["off", "40", "60", "70"],
            "availability_topic": "truma/status",
            "device": DEVICE_INFO,
            "icon": "mdi:water-boiler",
        })

        # Temperature sensors
        for sensor_id, name, topic, icon in [
            ("room_temp", "Room Temperature", "truma/room/current_temp", "mdi:thermometer"),
            ("water_temp", "Water Temperature", "truma/water/current_temp", "mdi:water-thermometer"),
            ("internal_temp", "Internal Temperature", "truma/system/internal_temp", "mdi:thermometer-lines"),
        ]:
            self._publish_config("sensor", sensor_id, {
                "name": name,
                "unique_id": f"truma_{sensor_id}",
                "state_topic": topic,
                "unit_of_measurement": "\u00b0C",
                "device_class": "temperature",
                "state_class": "measurement",
                "availability_topic": "truma/status",
                "device": DEVICE_INFO,
                "icon": icon,
            })

        # Energy source controls
        self._publish_config("switch", "diesel", {
            "name": "Diesel Heating",
            "unique_id": "truma_diesel",
            "command_topic": "truma/energy/diesel/set",
            "state_topic": "truma/energy/diesel/state",
            "payload_on": "1", "payload_off": "0",
            "state_on": "1", "state_off": "0",
            "availability_topic": "truma/status",
            "device": DEVICE_INFO,
            "icon": "mdi:fuel",
        })
        self._publish_config("select", "electric_level", {
            "name": "Electric Heating",
            "unique_id": "truma_electric_level",
            "command_topic": "truma/energy/electric/set",
            "state_topic": "truma/energy/electric/state",
            "options": ["Off", "900W", "1800W"],
            "availability_topic": "truma/status",
            "device": DEVICE_INFO,
            "icon": "mdi:lightning-bolt",
        })

        # Fan level
        self._publish_config("number", "fan_level", {
            "name": "Fan Level",
            "unique_id": "truma_fan_level",
            "command_topic": "truma/fan/level/set",
            "state_topic": "truma/fan/level/state",
            "min": 0, "max": 10, "step": 1,
            "availability_topic": "truma/status",
            "device": DEVICE_INFO,
            "icon": "mdi:fan",
        })

        # Binary sensors
        self._publish_config("binary_sensor", "flame", {
            "name": "Flame",
            "unique_id": "truma_flame",
            "state_topic": "truma/system/flame",
            "payload_on": "ON", "payload_off": "OFF",
            "device_class": "heat",
            "availability_topic": "truma/status",
            "device": DEVICE_INFO,
            "icon": "mdi:fire",
        })
        self._publish_config("binary_sensor", "connected", {
            "name": "BLE Connected",
            "unique_id": "truma_connected",
            "state_topic": "truma/system/connected",
            "payload_on": "ON", "payload_off": "OFF",
            "device_class": "connectivity",
            "availability_topic": "truma/status",
            "device": DEVICE_INFO,
        })

        log.info("Published HA discovery configs")

    def _publish_config(self, component, object_id, config):
        topic = f"homeassistant/{component}/truma_heater/{object_id}/config"
        self._client.publish(topic, json.dumps(config), retain=True)

    def _publish_state(self):
        """Publish current state to MQTT topics."""
        status = self._state_getter()
        if not status:
            return

        pub = self._client.publish

        # Connection
        pub("truma/system/connected", "ON" if status.get("connected") else "OFF", retain=True)

        # Room climate
        rc = status.get("room_climate")
        if rc:
            mode_map = {0: "off", 3: "heat", 5: "fan_only"}
            mode = mode_map.get(rc.get("mode"), "off")
            pub("truma/room/mode/state", mode, retain=True)
            if rc.get("target_temp_c") is not None:
                pub("truma/room/temp/state", str(rc["target_temp_c"]), retain=True)
            if rc.get("current_temp_c") is not None:
                pub("truma/room/current_temp", str(rc["current_temp_c"]), retain=True)

        # Air heating (for current temp if room_climate doesn't have it)
        ah = status.get("air_heating")
        if ah:
            if ah.get("current_temp_c") is not None:
                pub("truma/room/current_temp", str(ah["current_temp_c"]), retain=True)
            if ah.get("target_temp_c") is not None:
                pub("truma/room/temp/state", str(ah["target_temp_c"]), retain=True)

        # Water heating
        wh = status.get("water_heating")
        if wh:
            if wh.get("current_temp_c") is not None:
                pub("truma/water/current_temp", str(wh["current_temp_c"]), retain=True)
            mode_map = {0: "40", 1: "60", 2: "70"}
            active = wh.get("active", 0)
            mode = wh.get("mode")
            if active == 0 or mode is None:
                pub("truma/water/mode/state", "off", retain=True)
            else:
                pub("truma/water/mode/state", mode_map.get(mode, "off"), retain=True)

        # Energy
        en = status.get("energy")
        if en:
            pub("truma/energy/diesel/state", str(en.get("diesel", 0)), retain=True)
            elec_map = {0: "Off", 1: "900W", 2: "1800W"}
            pub("truma/energy/electric/state", elec_map.get(en.get("electric", 0), "Off"), retain=True)

        # Fan level (from air_heating)
        if ah and ah.get("fan_level") is not None:
            pub("truma/fan/level/state", str(ah["fan_level"]), retain=True)

        # System
        sys_data = status.get("system")
        if sys_data:
            flame = sys_data.get("flame_status")
            pub("truma/system/flame", "ON" if flame is not None and flame > 0 else "OFF", retain=True)
            if sys_data.get("internal_temp_c") is not None:
                pub("truma/system/internal_temp", str(sys_data["internal_temp_c"]), retain=True)

    def stop(self):
        self._running = False
        if self._client:
            self._client.publish("truma/status", "offline", retain=True)
            self._client.disconnect()
        log.info("MQTT stopped")
