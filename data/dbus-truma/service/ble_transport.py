"""BLE connection and transport layer for Truma iNetX.

Ported from scripts/truma_dbus.py (connection logic) and
scripts/test_protocol.py (transport FSM).
"""

import asyncio
import json
import logging
import uuid
from pathlib import Path

from dbus_fast.aio import MessageBus
from dbus_fast import BusType, Variant

from .const import (
    ADAPTER_PATH, BLUEZ, IDENTITY_FILE,
    CHAR_CMD, CHAR_DATA_W, CHAR_DATA_R, CHAR_CMD_ALT,
    DEV_APP_DEFAULT,
    TRANSPORT_INIT, TRANSPORT_READY, TRANSPORT_ACK,
    TRANSPORT_MSG_ACK, TRANSPORT_CONFIRM,
)
from .protocol import parse_v3_frame

log = logging.getLogger(__name__)


def _load_or_create_identity(path_str):
    """Load identity from file, or create and persist a new one."""
    path = Path(path_str)
    if path.exists():
        try:
            with open(path) as f:
                identity = json.load(f)
                log.info("Loaded identity: %s...", identity['muid'][:8])
                return identity
        except Exception as exc:
            log.warning("Failed to load identity, creating new one: %s", exc)

    identity = {
        "muid": str(uuid.uuid4()).upper(),
        "uuid": str(uuid.uuid4()).lower(),
        "username": "Vanlin Controller",
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, 'w') as f:
            json.dump(identity, f, indent=2)
        log.info("Created new identity: %s...", identity['muid'][:8])
    except Exception as exc:
        log.warning("Could not persist identity: %s", exc)
    return identity


class BleTransport:
    """BLE connection and transport layer for Truma iNetX."""

    def __init__(self):
        self.bus = None
        self.device = None
        self.chars = {}           # uuid -> dbus path
        self._char_ifaces = {}    # uuid -> cached interface
        self._transport_event = None
        self._transport_ack = None
        self._data_callbacks = []  # list of callbacks for decoded V3 frames
        self._send_lock = asyncio.Lock()
        self.assigned_addr = DEV_APP_DEFAULT
        self.identity = None
        self._connected = False
        self._om = None
        self._dev_path = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def connect(self):
        """Connect to Truma via BLE on hci1."""
        # 1. Connect to system D-Bus
        self.bus = await MessageBus(bus_type=BusType.SYSTEM).connect()

        # 2. Get ObjectManager
        om_intr = await self.bus.introspect(BLUEZ, "/")
        om_obj = self.bus.get_proxy_object(BLUEZ, "/", om_intr)
        self._om = om_obj.get_interface("org.freedesktop.DBus.ObjectManager")

        # 3. Try to find already-paired device first; scan if not found
        dev_path = await self._find_truma(paired_only=True)
        if not dev_path:
            log.info("No paired Truma found, scanning on %s...", ADAPTER_PATH)
            await self._scan_for_truma()
            dev_path = await self._find_truma(paired_only=False)

        if not dev_path:
            raise RuntimeError("Truma iNetX not found via BLE")

        self._dev_path = dev_path

        # Get device proxy
        intr = await self.bus.introspect(BLUEZ, dev_path)
        dev_obj = self.bus.get_proxy_object(BLUEZ, dev_path, intr)
        self.device = dev_obj.get_interface("org.bluez.Device1")
        self._dev_props = dev_obj.get_interface("org.freedesktop.DBus.Properties")

        # 4. Connect with retries (3 attempts, 3s delay)
        for attempt in range(3):
            try:
                log.info("Connecting (attempt %d)...", attempt + 1)
                await self.device.call_connect()
                log.info("Connected!")
                break
            except Exception as exc:
                if "Already Connected" in str(exc):
                    log.info("Already connected")
                    break
                if attempt < 2:
                    log.warning("  Retry: %s", exc)
                    await asyncio.sleep(3)
                else:
                    raise

        # 5. Wait for ServicesResolved (with initial delay for BLE setup)
        await asyncio.sleep(2)
        log.info("Waiting for ServicesResolved...")
        for i in range(30):
            try:
                resolved = await self._dev_props.call_get(BLUEZ + ".Device1", "ServicesResolved")
                if resolved.value:
                    log.info("ServicesResolved after %.1fs", i * 0.5)
                    break
            except Exception:
                pass
            await asyncio.sleep(0.5)
        else:
            log.warning("ServicesResolved not seen after 15s, trying discovery anyway")

        # 6. Discover characteristics (try even if not resolved — may be cached)
        await self._discover_chars(dev_path)
        if not self.chars:
            # Retry after reconnect — sometimes BlueZ needs a kick
            log.info("No chars found, forcing reconnect...")
            try:
                await self.device.call_disconnect()
                await asyncio.sleep(2)
                await self.device.call_connect()
                await asyncio.sleep(3)
                await self._discover_chars(dev_path)
            except Exception as e:
                log.warning("Reconnect attempt: %s", e)
        log.info("Found %d characteristics", len(self.chars))

        # 7. Subscribe notifications on CMD + DATA_R (NOT CMD_ALT)
        #    Force CCCD re-write: disable then enable
        await self._subscribe_notifications()

        # 8. Load/create identity
        self.identity = _load_or_create_identity(IDENTITY_FILE)
        self._connected = True

    async def send(self, packet: bytes) -> bool:
        """Send packet via transport protocol.

        Steps:
          1. InitDataTransfer: write [0x01, len_lo, len_hi] to CMD
          2. Wait for Ready (0x81 0x00) notification on CMD
          3. Send data to DATA_W without response
          4. Wait for DataAck (0xF0 0x01) notification on CMD
          5. Brief pause (0.2s) for async MsgAck
        Returns True if DataAck received.
        """
        async with self._send_lock:
            return await self._send_locked(packet)

    async def _send_locked(self, packet: bytes) -> bool:
        """Send packet (must be called under _send_lock)."""
        success = False
        try:
            # Step 1: InitDataTransfer
            announce = bytes([TRANSPORT_INIT, len(packet) & 0xFF, (len(packet) >> 8) & 0xFF])
            self._transport_event = asyncio.Event()
            self._transport_ack = None
            await self._write_char(CHAR_CMD, announce)

            # Step 2: Wait for Ready (0x81)
            try:
                await asyncio.wait_for(self._transport_event.wait(), timeout=3.0)
            except asyncio.TimeoutError:
                log.warning("[TRANSPORT] Timeout waiting for Ready ACK")
            self._transport_event.clear()

            # Step 3: Send data on DATA_W
            await self._write_char(CHAR_DATA_W, packet)

            # Step 4: Wait for DataAck (0xF0)
            try:
                await asyncio.wait_for(self._transport_event.wait(), timeout=3.0)
                if self._transport_ack and len(self._transport_ack) >= 1:
                    if self._transport_ack[0] == TRANSPORT_ACK:
                        success = True
            except asyncio.TimeoutError:
                log.warning("[TRANSPORT] Timeout waiting for DataAck")
            self._transport_event.clear()

            # Step 5: Brief pause for async MsgAck (handled in _handle_notification)
            await asyncio.sleep(0.2)

        except Exception as exc:
            log.error("[TRANSPORT] Error: %s", exc)
        finally:
            self._transport_event = None
            self._transport_ack = None

        return success

    def on_data(self, callback):
        """Register callback for decoded V3 data frames.

        Callback signature: callback(frame: dict)
        where frame is the result of parse_v3_frame().
        """
        self._data_callbacks.append(callback)

    async def disconnect(self):
        """Clean disconnect."""
        self._connected = False
        if self.device:
            try:
                await self.device.call_disconnect()
                log.info("Disconnected from Truma")
            except Exception as exc:
                log.warning("Disconnect error: %s", exc)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _scan_for_truma(self):
        """Run a BLE scan on ADAPTER_PATH for ~10 seconds."""
        intr = await self.bus.introspect(BLUEZ, ADAPTER_PATH)
        obj = self.bus.get_proxy_object(BLUEZ, ADAPTER_PATH, intr)
        adapter = obj.get_interface("org.bluez.Adapter1")
        try:
            await adapter.call_stop_discovery()
        except Exception:
            pass
        await asyncio.sleep(0.5)
        await adapter.call_set_discovery_filter({"Transport": Variant("s", "le")})
        await adapter.call_start_discovery()
        await asyncio.sleep(10)
        await adapter.call_stop_discovery()
        await asyncio.sleep(0.5)

    async def _find_truma(self, paired_only=False):
        """Find a Truma device in the BlueZ object tree."""
        objects = await self._om.call_get_managed_objects()
        for path, ifaces in objects.items():
            if "org.bluez.Device1" not in ifaces:
                continue
            dev = ifaces["org.bluez.Device1"]
            name = dev.get("Name")
            name = name.value if name else ""
            paired = dev.get("Paired")
            paired = paired.value if paired else False
            if paired_only and not paired:
                continue
            if "iNet" in str(name) or "ruma" in str(name):
                addr = dev.get("Address")
                addr = addr.value if addr else "?"
                log.info("Found: %s (%s) paired=%s", name, addr, paired)
                return path
        return None

    async def _discover_chars(self, dev_path):
        """Populate self.chars via the D-Bus ObjectManager."""
        objects = await self._om.call_get_managed_objects()
        for path, interfaces in objects.items():
            if not path.startswith(dev_path):
                continue
            if "org.bluez.GattCharacteristic1" not in interfaces:
                continue
            props = interfaces["org.bluez.GattCharacteristic1"]
            char_uuid = str(props["UUID"].value).lower()
            self.chars[char_uuid] = path

        for label, uuid_val in [
            ("CMD", CHAR_CMD), ("DATA_W", CHAR_DATA_W),
            ("DATA_R", CHAR_DATA_R), ("CMD_ALT", CHAR_CMD_ALT),
        ]:
            status = "OK" if uuid_val in self.chars else "MISSING"
            log.info("  %s: %s", label, status)

    async def _subscribe_notifications(self):
        """Enable notifications on CMD and DATA_R only (not CMD_ALT).

        Forces CCCD re-write by calling StopNotify then StartNotify.
        """
        # Build reverse lookup: D-Bus path -> char UUID
        self._path_to_uuid = {}

        for char_uuid in [CHAR_CMD, CHAR_DATA_R]:
            if char_uuid not in self.chars:
                log.warning("Char %s not found, skipping notify", char_uuid)
                continue
            path = self.chars[char_uuid]
            self._path_to_uuid[path] = char_uuid
            label = "CMD" if char_uuid == CHAR_CMD else "DATA_R"
            log.info("  Subscribing %s at %s", label, path)
            try:
                intr = await self.bus.introspect(BLUEZ, path)
                obj = self.bus.get_proxy_object(BLUEZ, path, intr)
                iface = obj.get_interface("org.bluez.GattCharacteristic1")
                props_iface = obj.get_interface("org.freedesktop.DBus.Properties")

                # Force CCCD re-write
                try:
                    await iface.call_stop_notify()
                    await asyncio.sleep(0.5)
                except Exception:
                    pass

                # Register per-object signal handler
                props_iface.on_properties_changed(self._make_notify_handler(char_uuid))

                # Also add explicit D-Bus match rule for this path
                await self.bus.call(
                    await self.bus.introspect("org.freedesktop.DBus", "/org/freedesktop/DBus").then(
                        lambda _: None
                    ) if False else None
                ) if False else None
                # dbus-fast should add the match rule via on_properties_changed,
                # but let's also try adding one explicitly
                try:
                    from dbus_fast import Message
                    match_rule = (
                        f"type='signal',"
                        f"interface='org.freedesktop.DBus.Properties',"
                        f"member='PropertiesChanged',"
                        f"path='{path}'"
                    )
                    reply = await self.bus.call(
                        Message(
                            destination="org.freedesktop.DBus",
                            path="/org/freedesktop/DBus",
                            interface="org.freedesktop.DBus",
                            member="AddMatch",
                            signature="s",
                            body=[match_rule],
                        )
                    )
                    log.info("  Added match rule for %s", label)
                except Exception as e:
                    log.warning("  Match rule for %s: %s", label, e)

                await iface.call_start_notify()
                await asyncio.sleep(1)
                log.info("  Subscribed %s OK", label)
            except Exception as exc:
                log.warning("  Notify failed %s: %s", char_uuid, exc)

    def _make_notify_handler(self, char_uuid):
        """Return a PropertiesChanged handler bound to char_uuid."""
        def handler(iface, changed, invalidated):
            if "Value" in changed:
                data = bytes(changed["Value"].value)
                self._handle_notification(char_uuid, data)
        return handler

    def _handle_notification(self, char_uuid, data):
        """Handle BLE notifications from CMD or DATA_R."""
        label = "CMD" if char_uuid == CHAR_CMD else "DATA" if char_uuid == CHAR_DATA_R else "???"
        log.info("[RX %s] %db: %s", label, len(data), data[:16].hex())

        if len(data) <= 4:
            # Check for MsgAck (0x83) FIRST — must auto-confirm with 0300
            if len(data) >= 1 and data[0] == TRANSPORT_MSG_ACK:
                log.info("  -> MsgAck, confirming 0300")
                asyncio.ensure_future(self._write_char(
                    CHAR_CMD, bytes([TRANSPORT_CONFIRM, 0x00])
                ))

            # Transport ACK (Ready, DataAck, etc.)
            self._transport_ack = data
            if self._transport_event:
                self._transport_event.set()
            return

        if char_uuid == CHAR_CMD:
            # Signal transport event for any CMD notification > 4 bytes
            if self._transport_event:
                self._transport_event.set()
            return

        # DATA_R: incoming V3 data frame from Truma
        # Auto-ACK with f001
        asyncio.ensure_future(self._write_char(
            CHAR_CMD, bytes([TRANSPORT_ACK, 0x01])
        ))

        # Parse and dispatch
        frame = parse_v3_frame(data)
        if frame is not None:
            for cb in self._data_callbacks:
                try:
                    cb(frame)
                except Exception as exc:
                    log.error("Data callback error: %s", exc)

    async def _get_char_iface(self, char_uuid):
        """Return cached GATT characteristic interface."""
        if char_uuid not in self._char_ifaces:
            path = self.chars[char_uuid]
            intr = await self.bus.introspect(BLUEZ, path)
            obj = self.bus.get_proxy_object(BLUEZ, path, intr)
            self._char_ifaces[char_uuid] = obj.get_interface("org.bluez.GattCharacteristic1")
        return self._char_ifaces[char_uuid]

    async def _write_char(self, char_uuid, data):
        """Write bytes to a GATT characteristic.

        CMD (fc314001) uses Write Request (with response).
        DATA_W (fc314002) uses Write Command (without response).
        """
        if char_uuid not in self.chars:
            log.error("Characteristic %s not found", char_uuid[-4:])
            return
        char = await self._get_char_iface(char_uuid)
        # CMD uses "request" (Write Request, ATT 0x12), DATA_W uses "command" (ATT 0x52)
        write_type = "request" if char_uuid == CHAR_CMD else "command"
        await char.call_write_value(bytes(data), {"type": Variant("s", write_type)})
