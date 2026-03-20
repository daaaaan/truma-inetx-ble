"""Try multiple pairing strategies with Truma."""
import asyncio
import os
from dbus_fast.aio import MessageBus
from dbus_fast import BusType, Variant
from dbus_fast.service import ServiceInterface, method

AGENT_PATH = "/test/agent"


class AutoAgent(ServiceInterface):
    """Agent that logs all callbacks and auto-accepts."""

    def __init__(self):
        super().__init__("org.bluez.Agent1")

    @method()
    def Release(self):
        print("[AGENT] Release")

    @method()
    def RequestPinCode(self, device: "o") -> "s":
        print("[AGENT] RequestPinCode: {}".format(device))
        return "0000"

    @method()
    def RequestPasskey(self, device: "o") -> "u":
        print("[AGENT] RequestPasskey: {}".format(device))
        return 0

    @method()
    def DisplayPasskey(self, device: "o", passkey: "u", entered: "q"):
        print("[AGENT] DisplayPasskey: {} code={} entered={}".format(device, passkey, entered))

    @method()
    def RequestConfirmation(self, device: "o", passkey: "u"):
        print("[AGENT] RequestConfirmation: {} code={}".format(device, passkey))

    @method()
    def RequestAuthorization(self, device: "o"):
        print("[AGENT] RequestAuthorization: {}".format(device))

    @method()
    def AuthorizeService(self, device: "o", uuid: "s"):
        print("[AGENT] AuthorizeService: {} {}".format(device, uuid))

    @method()
    def Cancel(self):
        print("[AGENT] Cancel called")


async def main():
    bus = await MessageBus(bus_type=BusType.SYSTEM).connect()

    # Register agent
    agent = AutoAgent()
    bus.export(AGENT_PATH, agent)

    am_intr = await bus.introspect("org.bluez", "/org/bluez")
    am_obj = bus.get_proxy_object("org.bluez", "/org/bluez", am_intr)
    agent_manager = am_obj.get_interface("org.bluez.AgentManager1")

    # Try KeyboardOnly - we type what Truma displays
    await agent_manager.call_register_agent(AGENT_PATH, "KeyboardOnly")
    await agent_manager.call_request_default_agent(AGENT_PATH)
    print("Agent registered as KeyboardOnly")

    om_intr = await bus.introspect("org.bluez", "/")
    om_obj = bus.get_proxy_object("org.bluez", "/", om_intr)
    om = om_obj.get_interface("org.freedesktop.DBus.ObjectManager")

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

    intr = await bus.introspect("org.bluez", truma_path)
    dev_obj = bus.get_proxy_object("org.bluez", truma_path, intr)
    device = dev_obj.get_interface("org.bluez.Device1")
    props = dev_obj.get_interface("org.freedesktop.DBus.Properties")

    # Set trusted first
    print("Setting trusted...")
    await props.call_set("org.bluez.Device1", "Trusted", Variant("b", True))

    # Strategy 1: Try connect first (may trigger pairing from device side)
    print("\n--- Strategy 1: Connect first ---")
    try:
        await device.call_connect()
        print("Connected without explicit pairing!")

        paired = await props.call_get("org.bluez.Device1", "Paired")
        print("Paired after connect: {}".format(paired.value))

        for i in range(30):
            resolved = await props.call_get("org.bluez.Device1", "ServicesResolved")
            if resolved.value:
                print("Services resolved ({}s)".format(i * 0.5))
                break
            await asyncio.sleep(0.5)

        # List characteristics
        objects = await om.call_get_managed_objects()
        char_count = 0
        for path, ifaces in objects.items():
            if not path.startswith(truma_path):
                continue
            if "org.bluez.GattCharacteristic1" in ifaces:
                c = ifaces["org.bluez.GattCharacteristic1"]
                uuid = c["UUID"].value.lower()
                print("  CHAR: {}".format(uuid))
                char_count += 1
        print("Total chars: {}".format(char_count))

        await device.call_disconnect()
        print("Disconnected")
        return

    except Exception as e:
        print("Connect failed: {}".format(e))

    # Strategy 2: Explicit pair
    print("\n--- Strategy 2: Explicit pair ---")
    try:
        await device.call_pair()
        print("Paired!")
    except Exception as e:
        print("Pair error: {}".format(e))
        paired = await props.call_get("org.bluez.Device1", "Paired")
        print("Paired status: {}".format(paired.value))

    # Strategy 3: Remove and re-try
    print("\n--- Strategy 3: Remove device and re-scan ---")
    try:
        aintr = await bus.introspect("org.bluez", "/org/bluez/hci1")
        aobj = bus.get_proxy_object("org.bluez", "/org/bluez/hci1", aintr)
        adapter2 = aobj.get_interface("org.bluez.Adapter1")
        await adapter2.call_remove_device(truma_path)
        print("Removed device, re-scanning...")
        await asyncio.sleep(2)
        await adapter2.call_start_discovery()
        await asyncio.sleep(8)

        objects = await om.call_get_managed_objects()
        for path, ifaces in objects.items():
            if "org.bluez.Device1" not in ifaces:
                continue
            dev = ifaces["org.bluez.Device1"]
            name = dev.get("Name")
            name = name.value if name else ""
            if "iNet" in name or "ruma" in name:
                print("Re-found: {}".format(name))
                truma_path = path
                break

        await adapter2.call_stop_discovery()

        if truma_path:
            intr = await bus.introspect("org.bluez", truma_path)
            dev_obj = bus.get_proxy_object("org.bluez", truma_path, intr)
            device = dev_obj.get_interface("org.bluez.Device1")
            props = dev_obj.get_interface("org.freedesktop.DBus.Properties")
            await props.call_set("org.bluez.Device1", "Trusted", Variant("b", True))

            print("Trying connect on fresh device...")
            try:
                await device.call_connect()
                print("CONNECTED!")
                paired = await props.call_get("org.bluez.Device1", "Paired")
                print("Paired: {}".format(paired.value))
            except Exception as e:
                print("Connect: {}".format(e))

            print("Trying pair on fresh device...")
            try:
                await device.call_pair()
                print("PAIRED!")
            except Exception as e:
                print("Pair: {}".format(e))

    except Exception as e:
        print("Strategy 3 error: {}".format(e))

asyncio.run(main())
