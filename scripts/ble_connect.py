import asyncio
from dbus_fast.aio import MessageBus
from dbus_fast import BusType, Variant

async def main():
    bus = await MessageBus(bus_type=BusType.SYSTEM).connect()

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
    print("Paired: {}".format(paired.value))

    if not paired.value:
        print("Pairing... CHECK TRUMA PANEL FOR PASSKEY!")
        try:
            await device.call_pair()
            print("Paired!")
        except Exception as e:
            print("Pair: {}".format(e))

    print("Connecting...")
    try:
        await device.call_connect()
        print("Connected!")
    except Exception as e:
        if "Already Connected" in str(e):
            print("Already connected")
        else:
            print("Connect: {}".format(e))
            return

    for i in range(30):
        resolved = await props.call_get("org.bluez.Device1", "ServicesResolved")
        if resolved.value:
            print("Services resolved ({}s)".format(i * 0.5))
            break
        await asyncio.sleep(0.5)
    else:
        print("Services NOT resolved after 15s")
        return

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

    print("\nTotal chars: {}".format(len(chars)))
    target_uuids = [
        ("CMD  4001", "fc314001-f3b2-11e8-8eb2-f2801f1b9fd1"),
        ("DATAW 4002", "fc314002-f3b2-11e8-8eb2-f2801f1b9fd1"),
        ("DATAR 4003", "fc314003-f3b2-11e8-8eb2-f2801f1b9fd1"),
        ("CMD2  4004", "fc314004-f3b2-11e8-8eb2-f2801f1b9fd1"),
        ("SVC_R 0100", "f47b0100-f3b2-11e8-8eb2-f2801f1b9fd1"),
        ("SVC_W 0101", "f47b0101-f3b2-11e8-8eb2-f2801f1b9fd1"),
    ]
    for label, uuid in target_uuids:
        found = "YES" if uuid in chars else "NO"
        print("  {}: {}".format(label, found))

    await device.call_disconnect()
    print("Disconnected")

asyncio.run(main())
