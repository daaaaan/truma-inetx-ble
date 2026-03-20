"""Pair with Truma iNetX using BlueZ D-Bus agent for passkey entry."""
import asyncio
import sys
from dbus_fast.aio import MessageBus
from dbus_fast import BusType, Variant
from dbus_fast.service import ServiceInterface, method

AGENT_PATH = "/test/agent"


class PairingAgent(ServiceInterface):
    """BlueZ pairing agent that handles passkey entry."""

    def __init__(self, passkey):
        super().__init__("org.bluez.Agent1")
        self._passkey = passkey

    @method()
    def Release(self):
        print("Agent released")

    @method()
    def RequestPinCode(self, device: "o") -> "s":
        print("RequestPinCode for {}".format(device))
        return str(self._passkey)

    @method()
    def DisplayPinCode(self, device: "o", pincode: "s"):
        print("DisplayPinCode: {} -> {}".format(device, pincode))

    @method()
    def RequestPasskey(self, device: "o") -> "u":
        print("RequestPasskey for {} -> returning {}".format(device, self._passkey))
        return self._passkey

    @method()
    def DisplayPasskey(self, device: "o", passkey: "u", entered: "q"):
        print("DisplayPasskey: {} -> {} (entered: {})".format(device, passkey, entered))

    @method()
    def RequestConfirmation(self, device: "o", passkey: "u"):
        print("RequestConfirmation: {} passkey={}".format(device, passkey))
        # Auto-confirm

    @method()
    def RequestAuthorization(self, device: "o"):
        print("RequestAuthorization: {}".format(device))

    @method()
    def AuthorizeService(self, device: "o", uuid: "s"):
        print("AuthorizeService: {} uuid={}".format(device, uuid))

    @method()
    def Cancel(self):
        print("Pairing cancelled by remote")


async def main():
    if len(sys.argv) < 2:
        print("Usage: python3 ble_pair.py <passkey>")
        print("  passkey: 6-digit number shown on Truma panel")
        return

    passkey = int(sys.argv[1])
    print("Using passkey: {}".format(passkey))

    bus = await MessageBus(bus_type=BusType.SYSTEM).connect()

    # Register agent
    agent = PairingAgent(passkey)
    bus.export(AGENT_PATH, agent)

    # Register with BlueZ agent manager
    am_intr = await bus.introspect("org.bluez", "/org/bluez")
    am_obj = bus.get_proxy_object("org.bluez", "/org/bluez", am_intr)
    agent_manager = am_obj.get_interface("org.bluez.AgentManager1")
    await agent_manager.call_register_agent(AGENT_PATH, "KeyboardDisplay")
    await agent_manager.call_request_default_agent(AGENT_PATH)
    print("Agent registered")

    # Get object manager
    om_intr = await bus.introspect("org.bluez", "/")
    om_obj = bus.get_proxy_object("org.bluez", "/", om_intr)
    om = om_obj.get_interface("org.freedesktop.DBus.ObjectManager")

    # Scan
    intr = await bus.introspect("org.bluez", "/org/bluez/hci1")
    obj = bus.get_proxy_object("org.bluez", "/org/bluez/hci1", intr)
    adapter = obj.get_interface("org.bluez.Adapter1")

    print("Scanning...")
    await adapter.call_start_discovery()
    await asyncio.sleep(10)

    objects = await om.call_get_managed_objects()
    truma_path = None
    for path, ifaces in objects.items():
        if "org.bluez.Device1" not in ifaces:
            continue
        dev = ifaces["org.bluez.Device1"]
        name = dev.get("Name")
        name = name.value if name else ""
        if "iNet" in name or "ruma" in name:
            addr = dev.get("Address")
            addr = addr.value if addr else "?"
            print("Found: {} ({})".format(name, addr))
            truma_path = path
            break

    await adapter.call_stop_discovery()

    if not truma_path:
        print("Truma not found!")
        return

    # Pair
    intr = await bus.introspect("org.bluez", truma_path)
    dev_obj = bus.get_proxy_object("org.bluez", truma_path, intr)
    device = dev_obj.get_interface("org.bluez.Device1")
    props = dev_obj.get_interface("org.freedesktop.DBus.Properties")

    paired = await props.call_get("org.bluez.Device1", "Paired")
    if paired.value:
        print("Already paired!")
    else:
        print("Initiating pairing with passkey {}...".format(passkey))
        try:
            await device.call_pair()
            print("PAIRED SUCCESSFULLY!")
        except Exception as e:
            print("Pair error: {}".format(e))
            return

    # Connect
    print("Connecting...")
    try:
        await device.call_connect()
        print("Connected!")
    except Exception as e:
        if "Already Connected" in str(e):
            print("Already connected")
        else:
            print("Connect error: {}".format(e))
            return

    # Wait for services
    for i in range(30):
        resolved = await props.call_get("org.bluez.Device1", "ServicesResolved")
        if resolved.value:
            print("Services resolved ({}s)".format(i * 0.5))
            break
        await asyncio.sleep(0.5)
    else:
        print("Services NOT resolved")
        return

    # List characteristics
    objects = await om.call_get_managed_objects()
    chars = {}
    for path, ifaces in objects.items():
        if not path.startswith(truma_path):
            continue
        if "org.bluez.GattCharacteristic1" in ifaces:
            c = ifaces["org.bluez.GattCharacteristic1"]
            uuid = c["UUID"].value.lower()
            chars[uuid] = path
            print("  CHAR: {}".format(uuid))

    print("\nCharacteristic check:")
    for label, uuid in [
        ("CMD  4001", "fc314001-f3b2-11e8-8eb2-f2801f1b9fd1"),
        ("DATAW 4002", "fc314002-f3b2-11e8-8eb2-f2801f1b9fd1"),
        ("DATAR 4003", "fc314003-f3b2-11e8-8eb2-f2801f1b9fd1"),
        ("CMD2  4004", "fc314004-f3b2-11e8-8eb2-f2801f1b9fd1"),
        ("SVC_R 0100", "f47b0100-f3b2-11e8-8eb2-f2801f1b9fd1"),
        ("SVC_W 0101", "f47b0101-f3b2-11e8-8eb2-f2801f1b9fd1"),
    ]:
        found = "YES" if uuid in chars else "NO"
        print("  {}: {}".format(label, found))

    # Keep connected briefly then disconnect
    print("\nStaying connected 5s to verify stability...")
    await asyncio.sleep(5)
    await device.call_disconnect()
    print("Disconnected. Pairing persisted for next connection.")

asyncio.run(main())
