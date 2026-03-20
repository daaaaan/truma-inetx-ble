"""Two-phase Truma pairing: initiate pair, wait for passkey via file."""
import asyncio
import os
import sys
from dbus_fast.aio import MessageBus
from dbus_fast import BusType, Variant
from dbus_fast.service import ServiceInterface, method

AGENT_PATH = "/test/agent"
PASSKEY_FILE = "/tmp/truma_passkey"


class PairingAgent(ServiceInterface):
    """Agent that reads passkey from file when BlueZ requests it."""

    def __init__(self):
        super().__init__("org.bluez.Agent1")

    @method()
    def Release(self):
        print("Agent released")

    @method()
    def RequestPinCode(self, device: "o") -> "s":
        pk = self._wait_for_passkey()
        print("Returning PinCode: {}".format(pk))
        return str(pk)

    @method()
    def RequestPasskey(self, device: "o") -> "u":
        print("*** PASSKEY REQUESTED - Truma should be showing code now ***")
        print("*** Write passkey to {} or wait... ***".format(PASSKEY_FILE))
        pk = self._wait_for_passkey()
        print("Returning passkey: {}".format(pk))
        return pk

    @method()
    def DisplayPasskey(self, device: "o", passkey: "u", entered: "q"):
        print("DisplayPasskey: {} (entered: {})".format(passkey, entered))

    @method()
    def RequestConfirmation(self, device: "o", passkey: "u"):
        print("Auto-confirming passkey: {}".format(passkey))

    @method()
    def RequestAuthorization(self, device: "o"):
        print("Auto-authorizing")

    @method()
    def AuthorizeService(self, device: "o", uuid: "s"):
        print("Auto-authorizing service: {}".format(uuid))

    @method()
    def Cancel(self):
        print("Pairing cancelled")

    def _wait_for_passkey(self):
        """Poll for passkey file, up to 120s."""
        import time
        for i in range(120):
            if os.path.exists(PASSKEY_FILE):
                with open(PASSKEY_FILE) as f:
                    pk = f.read().strip()
                os.unlink(PASSKEY_FILE)
                return int(pk)
            time.sleep(1)
            if i % 10 == 0 and i > 0:
                print("  Still waiting for passkey file... ({}s)".format(i))
        raise Exception("Passkey timeout")


async def main():
    # Clean up old passkey file
    if os.path.exists(PASSKEY_FILE):
        os.unlink(PASSKEY_FILE)

    bus = await MessageBus(bus_type=BusType.SYSTEM).connect()

    # Register agent
    agent = PairingAgent()
    bus.export(AGENT_PATH, agent)

    am_intr = await bus.introspect("org.bluez", "/org/bluez")
    am_obj = bus.get_proxy_object("org.bluez", "/org/bluez", am_intr)
    agent_manager = am_obj.get_interface("org.bluez.AgentManager1")
    await agent_manager.call_register_agent(AGENT_PATH, "KeyboardDisplay")
    await agent_manager.call_request_default_agent(AGENT_PATH)
    print("Agent registered")

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

    paired = await props.call_get("org.bluez.Device1", "Paired")
    if paired.value:
        print("Already paired!")
    else:
        print("")
        print("=" * 50)
        print("INITIATING PAIRING - check Truma panel for passkey!")
        print("Then run on another terminal:")
        print("  echo PASSKEY > /tmp/truma_passkey")
        print("=" * 50)
        print("")
        try:
            await device.call_pair()
            print("PAIRED SUCCESSFULLY!")
        except Exception as e:
            print("Pair result: {}".format(e))
            # Check if we actually paired despite error
            paired = await props.call_get("org.bluez.Device1", "Paired")
            if paired.value:
                print("Actually paired despite error!")
            else:
                print("Pairing failed")
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

    for i in range(30):
        resolved = await props.call_get("org.bluez.Device1", "ServicesResolved")
        if resolved.value:
            print("Services resolved ({}s)".format(i * 0.5))
            break
        await asyncio.sleep(0.5)
    else:
        print("Services NOT resolved")
        return

    objects = await om.call_get_managed_objects()
    for path, ifaces in objects.items():
        if not path.startswith(truma_path):
            continue
        if "org.bluez.GattCharacteristic1" in ifaces:
            c = ifaces["org.bluez.GattCharacteristic1"]
            uuid = c["UUID"].value.lower()
            print("  CHAR: {}".format(uuid))

    print("\nPairing and connection successful!")
    print("Device will stay paired for future connections.")

    await device.call_disconnect()
    print("Disconnected (pairing saved)")

asyncio.run(main())
