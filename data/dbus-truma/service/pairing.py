"""BLE scanning and pairing for Truma iNetX via BlueZ D-Bus."""
import asyncio
import json
import logging
from pathlib import Path

from dbus_fast.aio import MessageBus
from dbus_fast import BusType, Variant
from dbus_fast.service import ServiceInterface, method

from .const import BLUEZ, IDENTITY_FILE

log = logging.getLogger("truma.pairing")

AGENT_PATH = "/truma/agent"


class PairingAgent(ServiceInterface):
    """BlueZ pairing agent that auto-responds with a passkey."""

    def __init__(self, passkey):
        super().__init__("org.bluez.Agent1")
        self._passkey = passkey

    @method()
    def Release(self):
        log.info("Agent released")

    @method()
    def RequestPinCode(self, device: "o") -> "s":
        log.info("RequestPinCode for %s", device)
        return str(self._passkey)

    @method()
    def DisplayPinCode(self, device: "o", pincode: "s"):
        log.info("DisplayPinCode: %s -> %s", device, pincode)

    @method()
    def RequestPasskey(self, device: "o") -> "u":
        log.info("RequestPasskey for %s -> %d", device, self._passkey)
        return self._passkey

    @method()
    def DisplayPasskey(self, device: "o", passkey: "u", entered: "q"):
        log.info("DisplayPasskey: %s -> %d", device, passkey)

    @method()
    def RequestConfirmation(self, device: "o", passkey: "u"):
        log.info("RequestConfirmation: %s passkey=%d", device, passkey)

    @method()
    def RequestAuthorization(self, device: "o"):
        log.info("RequestAuthorization: %s", device)

    @method()
    def AuthorizeService(self, device: "o", uuid: "s"):
        log.info("AuthorizeService: %s uuid=%s", device, uuid)

    @method()
    def Cancel(self):
        log.info("Pairing cancelled by remote")


class BlePairing:
    """BLE scanning and pairing operations."""

    def __init__(self, adapter_path="/org/bluez/hci1"):
        self.adapter_path = adapter_path
        self._bus = None
        self._om = None

    async def _get_bus(self):
        if self._bus is None or not self._bus.connected:
            self._bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
            om_intr = await self._bus.introspect(BLUEZ, "/")
            om_obj = self._bus.get_proxy_object(BLUEZ, "/", om_intr)
            self._om = om_obj.get_interface("org.freedesktop.DBus.ObjectManager")
        return self._bus

    async def get_adapters(self) -> list:
        """List available BLE adapters."""
        bus = await self._get_bus()
        objects = await self._om.call_get_managed_objects()
        adapters = []
        for path, ifaces in objects.items():
            if "org.bluez.Adapter1" not in ifaces:
                continue
            props = ifaces["org.bluez.Adapter1"]
            adapters.append({
                "path": path,
                "name": props.get("Name", Variant("s", "")).value,
                "address": props.get("Address", Variant("s", "")).value,
                "powered": props.get("Powered", Variant("b", False)).value,
                "discovering": props.get("Discovering", Variant("b", False)).value,
            })
        return adapters

    async def scan(self, duration=10) -> list:
        """Scan for Truma devices. Returns list of found devices."""
        bus = await self._get_bus()

        # Get adapter
        intr = await bus.introspect(BLUEZ, self.adapter_path)
        obj = bus.get_proxy_object(BLUEZ, self.adapter_path, intr)
        adapter = obj.get_interface("org.bluez.Adapter1")

        # Stop any existing scan
        try:
            await adapter.call_stop_discovery()
        except Exception:
            pass
        await asyncio.sleep(0.5)

        # Start LE scan
        await adapter.call_set_discovery_filter({"Transport": Variant("s", "le")})
        await adapter.call_start_discovery()
        await asyncio.sleep(duration)
        await adapter.call_stop_discovery()
        await asyncio.sleep(0.5)

        # Find Truma devices
        objects = await self._om.call_get_managed_objects()
        devices = []
        for path, ifaces in objects.items():
            if "org.bluez.Device1" not in ifaces:
                continue
            dev = ifaces["org.bluez.Device1"]
            name = dev.get("Name")
            name = name.value if name else ""
            if not name:
                continue
            addr = dev.get("Address", Variant("s", "")).value
            paired = dev.get("Paired", Variant("b", False)).value
            connected = dev.get("Connected", Variant("b", False)).value
            rssi = dev.get("RSSI", Variant("n", 0)).value
            is_truma = "iNet" in name or "ruma" in name
            if is_truma:
                devices.append({
                    "path": path,
                    "name": name,
                    "address": addr,
                    "paired": paired,
                    "connected": connected,
                    "rssi": rssi,
                })
        return devices

    async def pair(self, address: str, passkey: int) -> dict:
        """Pair with a Truma device using the given passkey.

        Returns {"ok": bool, "message": str}
        """
        bus = await self._get_bus()

        # Find device path
        objects = await self._om.call_get_managed_objects()
        dev_path = None
        for path, ifaces in objects.items():
            if "org.bluez.Device1" not in ifaces:
                continue
            dev = ifaces["org.bluez.Device1"]
            addr = dev.get("Address")
            addr = addr.value if addr else ""
            if addr.upper() == address.upper():
                dev_path = path
                break

        if not dev_path:
            return {"ok": False, "message": f"Device {address} not found. Run a scan first."}

        # Register pairing agent
        agent = PairingAgent(passkey)
        bus.export(AGENT_PATH, agent)

        try:
            am_intr = await bus.introspect(BLUEZ, "/org/bluez")
            am_obj = bus.get_proxy_object(BLUEZ, "/org/bluez", am_intr)
            agent_mgr = am_obj.get_interface("org.bluez.AgentManager1")
            await agent_mgr.call_register_agent(AGENT_PATH, "KeyboardDisplay")
            await agent_mgr.call_request_default_agent(AGENT_PATH)
        except Exception as e:
            if "Already Exists" not in str(e):
                return {"ok": False, "message": f"Agent registration failed: {e}"}

        # Get device and check if already paired
        intr = await bus.introspect(BLUEZ, dev_path)
        dev_obj = bus.get_proxy_object(BLUEZ, dev_path, intr)
        device = dev_obj.get_interface("org.bluez.Device1")
        props = dev_obj.get_interface("org.freedesktop.DBus.Properties")

        # Set trusted
        try:
            await props.call_set("org.bluez.Device1", "Trusted", Variant("b", True))
        except Exception:
            pass

        paired = await props.call_get("org.bluez.Device1", "Paired")
        if paired.value:
            return {"ok": True, "message": "Already paired"}

        # Pair
        try:
            await device.call_pair()
            return {"ok": True, "message": "Paired successfully"}
        except Exception as e:
            return {"ok": False, "message": f"Pairing failed: {e}"}
        finally:
            try:
                await agent_mgr.call_unregister_agent(AGENT_PATH)
            except Exception:
                pass
            try:
                bus.unexport(AGENT_PATH)
            except Exception:
                pass

    async def unpair(self, address: str) -> dict:
        """Remove pairing for a device."""
        bus = await self._get_bus()

        objects = await self._om.call_get_managed_objects()
        dev_path = None
        for path, ifaces in objects.items():
            if "org.bluez.Device1" not in ifaces:
                continue
            dev = ifaces["org.bluez.Device1"]
            addr = dev.get("Address")
            addr = addr.value if addr else ""
            if addr.upper() == address.upper():
                dev_path = path
                break

        if not dev_path:
            return {"ok": False, "message": "Device not found"}

        try:
            intr = await bus.introspect(BLUEZ, self.adapter_path)
            obj = bus.get_proxy_object(BLUEZ, self.adapter_path, intr)
            adapter = obj.get_interface("org.bluez.Adapter1")
            await adapter.call_remove_device(dev_path)
            return {"ok": True, "message": "Device removed"}
        except Exception as e:
            return {"ok": False, "message": f"Remove failed: {e}"}

    @staticmethod
    def get_identity() -> dict:
        """Get current identity info."""
        path = Path(IDENTITY_FILE)
        if path.exists():
            try:
                with open(path) as f:
                    identity = json.load(f)
                return {
                    "exists": True,
                    "muid": identity.get("muid", "")[:8] + "...",
                    "username": identity.get("username", ""),
                    "file": str(path),
                }
            except Exception:
                pass
        return {"exists": False, "file": str(path)}

    @staticmethod
    def reset_identity() -> dict:
        """Delete identity file. New one will be created on next connect."""
        path = Path(IDENTITY_FILE)
        if path.exists():
            path.unlink()
            return {"ok": True, "message": "Identity reset. Re-pair required."}
        return {"ok": True, "message": "No identity file found"}
