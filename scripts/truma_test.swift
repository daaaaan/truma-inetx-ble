import Foundation
import CoreBluetooth

let TRUMA_SERVICE = CBUUID(string: "FC314000-F3B2-11E8-8EB2-F2801F1B9FD1")
let CHAR_CMD      = CBUUID(string: "FC314001-F3B2-11E8-8EB2-F2801F1B9FD1")
let CHAR_DATA_W   = CBUUID(string: "FC314002-F3B2-11E8-8EB2-F2801F1B9FD1")
let CHAR_DATA_R   = CBUUID(string: "FC314003-F3B2-11E8-8EB2-F2801F1B9FD1")
let CHAR_CMD_ALT  = CBUUID(string: "FC314004-F3B2-11E8-8EB2-F2801F1B9FD1")

class TrumaTest: NSObject, CBCentralManagerDelegate, CBPeripheralDelegate {
    let bleQueue = DispatchQueue(label: "ble", qos: .userInitiated)
    let testQueue = DispatchQueue(label: "test", qos: .userInitiated)
    var central: CBCentralManager!
    var peripheral: CBPeripheral?
    var chars: [CBUUID: CBCharacteristic] = [:]
    var notifyCount = 0
    var writeError: Error?
    var writeSem = DispatchSemaphore(value: 0)
    var notifySem = DispatchSemaphore(value: 0)
    var rxData: [Data] = []
    var testStarted = false

    override init() {
        super.init()
        central = CBCentralManager(delegate: self, queue: bleQueue)
    }

    // MARK: - CBOR helpers
    func cborMap(_ dict: [(String, Any)]) -> Data {
        var d = Data()
        d.append(0xA0 | UInt8(dict.count))
        for (key, val) in dict {
            cborStr(key, &d)
            cborVal(val, &d)
        }
        return d
    }
    func cborStr(_ s: String, _ d: inout Data) {
        let b = Array(s.utf8)
        if b.count < 24 { d.append(0x60 | UInt8(b.count)) }
        else { d.append(0x78); d.append(UInt8(b.count)) }
        d.append(contentsOf: b)
    }
    func cborVal(_ v: Any, _ d: inout Data) {
        if let i = v as? Int {
            if i < 24 { d.append(UInt8(i)) }
            else if i < 256 { d.append(0x18); d.append(UInt8(i)) }
            else if i < 65536 { d.append(0x19); d.append(UInt8(i >> 8)); d.append(UInt8(i & 0xFF)) }
            else { d.append(0x1A); d.append(contentsOf: withUnsafeBytes(of: UInt32(i).bigEndian) { Array($0) }) }
        } else if let s = v as? String { cborStr(s, &d) }
        else if let a = v as? [Int] {
            d.append(0x80 | UInt8(a.count))
            for i in a { cborVal(i, &d) }
        } else if let a = v as? [String] {
            if a.count < 24 { d.append(0x80 | UInt8(a.count)) }
            else { d.append(0x98); d.append(UInt8(a.count)) }
            for s in a { cborStr(s, &d) }
        }
    }

    // MARK: - V3 Frame
    func v3Frame(dest: UInt16, src: UInt16, ctrl: UInt8, sub: UInt8, corr: UInt8, cbor: Data) -> Data {
        var f = Data()
        f.append(contentsOf: withUnsafeBytes(of: dest.littleEndian) { Array($0) })
        f.append(contentsOf: withUnsafeBytes(of: src.littleEndian) { Array($0) })
        let pktSz = UInt16(cbor.count + 2 + 9)
        f.append(contentsOf: withUnsafeBytes(of: pktSz.littleEndian) { Array($0) })
        f.append(ctrl)
        f.append(contentsOf: [UInt8](repeating: 0, count: 9))
        f.append(sub)
        f.append(corr)
        f.append(cbor)
        return f
    }

    // MARK: - Write helpers
    // Writes dispatch to bleQueue, waits happen on testQueue
    func writeCmd(_ data: Data) -> Error? {
        guard let c = chars[CHAR_CMD] else { return NSError(domain: "BLE", code: -1) }
        writeError = nil
        writeSem = DispatchSemaphore(value: 0)
        bleQueue.async {
            self.peripheral?.writeValue(data, for: c, type: .withResponse)
        }
        let _ = writeSem.wait(timeout: .now() + 5.0)
        return writeError
    }

    func writeData(_ data: Data) {
        guard let c = chars[CHAR_DATA_W] else { return }
        bleQueue.async {
            self.peripheral?.writeValue(data, for: c, type: .withoutResponse)
        }
        Thread.sleep(forTimeInterval: 0.05) // brief settle
    }

    func waitNotify(timeout: TimeInterval = 5.0) -> Data? {
        notifySem = DispatchSemaphore(value: 0)
        let result = notifySem.wait(timeout: .now() + timeout)
        if result == .timedOut {
            print("  [timeout waiting for notify]")
            return nil
        }
        return rxData.last
    }

    // MARK: - Transport send
    func sendTransport(_ packet: Data) -> Bool {
        let announce = Data([0x01, UInt8(packet.count & 0xFF), UInt8(packet.count >> 8)])
        print("  TX CMD: \(announce.hex)  [InitDataTransfer sz=\(packet.count)]")

        if let err = writeCmd(announce) {
            print("  [CMD ERR] \(err.localizedDescription)")
            return false
        }

        // Wait for 0x8100 ready
        if let ack = waitNotify(timeout: 3), ack.count >= 2 {
            print("  RX CMD: \(ack.hex)")
        }

        // Send data
        print("  TX DATA (\(packet.count)b): \(packet.prefix(30).hex)...")
        writeData(packet)

        // Wait for 0xf001
        if let ack = waitNotify(timeout: 3), ack.count >= 2 {
            print("  RX: \(ack.hex)")
        }

        // Wait for 0x83xx00 and confirm
        if let ack = waitNotify(timeout: 3), ack.count > 0, ack[0] == 0x83 {
            print("  RX: \(ack.hex) -> TX confirm 0300")
            let _ = writeCmd(Data([0x03, 0x00]))
        }

        return true
    }

    // MARK: - Run tests (on testQueue, NOT bleQueue)
    func runTests() {
        testQueue.async { self._runTests() }
    }

    func _runTests() {
        print("\n" + String(repeating: "=", count: 50))
        print("  TRUMA PROTOCOL TEST")
        print(String(repeating: "=", count: 50))

        // Strategy A: Try transport protocol first
        print("\n--- A: Transport write test ---")
        let testData = Data([0x01, 0x05, 0x00])
        if let err = writeCmd(testData) {
            print("  CMD write failed (code \((err as NSError).code))")
            print("  Falling back to DIRECT DATA writes (no transport)")
            print("")
            _runDirectTests()
            return
        }
        print("  CMD write OK! Using transport protocol.")
        // Abort the test transport (send 0 bytes)
        Thread.sleep(forTimeInterval: 1)
        _runTransportTests()

    }

    func _runDirectTests() {
        // Write V3 frames directly to DATA_WRITE without CMD handshake
        print("--- Direct: Registration ---")
        let regCbor = cborMap([("pv", [5, 1] as Any)])
        let regFrame = v3Frame(dest: 0xFFFF, src: 0x0500, ctrl: 0x01, sub: 0x01, corr: 0x42, cbor: regCbor)
        print("  TX DATA_W (\(regFrame.count)b): \(regFrame.hex)")
        writeData(regFrame)
        Thread.sleep(forTimeInterval: 2)
        printNewNotifications("Registration")

        print("\n--- Direct: Subscribe ---")
        let topics: [String] = ["AirHeating", "WaterHeating", "RoomClimate",
                                 "EnergySrc", "Temperature", "PowerSupply"]
        var subCbor = Data()
        subCbor.append(0xA1)
        cborStr("tn", &subCbor)
        subCbor.append(0x86)
        for t in topics { cborStr(t, &subCbor) }
        let subFrame = v3Frame(dest: 0x0000, src: 0x0500, ctrl: 0x03, sub: 0x02, corr: 0, cbor: subCbor)
        print("  TX DATA_W (\(subFrame.count)b)")
        writeData(subFrame)
        Thread.sleep(forTimeInterval: 2)
        printNewNotifications("Subscribe")

        print("\n--- Direct: SystemTime ---")
        let ts = Int(Date().timeIntervalSince1970)
        let timeCbor = cborMap([("tn", "SystemTime" as Any), ("pn", "Time" as Any), ("v", ts as Any)])
        let timeFrame = v3Frame(dest: 0x0101, src: 0x0500, ctrl: 0x03, sub: 0x01, corr: 0, cbor: timeCbor)
        writeData(timeFrame)

        let nameCbor = cborMap([("tn", "MobileIdentity" as Any), ("pn", "UserName" as Any), ("v", "Vanlin" as Any)])
        let nameFrame = v3Frame(dest: 0x0101, src: 0x0500, ctrl: 0x03, sub: 0x01, corr: 0, cbor: nameCbor)
        writeData(nameFrame)

        let lastCbor = cborMap([("LastMessage", 1 as Any)])
        let lastFrame = v3Frame(dest: 0x0500, src: 0x0500, ctrl: 0x03, sub: 0x01, corr: 0, cbor: lastCbor)
        writeData(lastFrame)
        Thread.sleep(forTimeInterval: 2)
        printNewNotifications("Init sequence")

        // Listen for data
        print("\n--- Listening 20s for updates ---")
        Thread.sleep(forTimeInterval: 20)
        printNewNotifications("Listen")
        finish()
    }

    func _runTransportTests() {
        print("\n--- Transport: Registration ---")
        let regCbor = cborMap([("pv", [5, 1] as Any)])
        let regFrame = v3Frame(dest: 0xFFFF, src: 0x0500, ctrl: 0x01, sub: 0x01, corr: 0x42, cbor: regCbor)
        let _ = sendTransport(regFrame)
        Thread.sleep(forTimeInterval: 2)
        printNewNotifications("Registration")

        print("\n--- Transport: Subscribe ---")
        let topics: [String] = ["AirHeating", "WaterHeating", "RoomClimate",
                                 "EnergySrc", "Temperature", "PowerSupply"]
        var subCbor = Data()
        subCbor.append(0xA1)
        cborStr("tn", &subCbor)
        subCbor.append(0x86)
        for t in topics { cborStr(t, &subCbor) }
        let subFrame = v3Frame(dest: 0x0000, src: 0x0500, ctrl: 0x03, sub: 0x02, corr: 0, cbor: subCbor)
        let _ = sendTransport(subFrame)
        Thread.sleep(forTimeInterval: 2)
        printNewNotifications("Subscribe")

        print("\n--- Listening 15s ---")
        Thread.sleep(forTimeInterval: 15)
        printNewNotifications("Listen")
        finish()
    }

    var lastPrintedIdx = 0
    func printNewNotifications(_ label: String) {
        let newData = Array(rxData[lastPrintedIdx...])
        print("  [\(label)] \(newData.count) new notifications:")
        for (i, d) in newData.enumerated() {
            if d.count > 16 {
                let dest = UInt16(d[0]) | (UInt16(d[1]) << 8)
                let src = UInt16(d[2]) | (UInt16(d[3]) << 8)
                let ctrl = d[6]
                print("    V3: dst=0x\(String(format:"%04X",dest)) src=0x\(String(format:"%04X",src)) ctrl=0x\(String(format:"%02X",ctrl)) (\(d.count)b)")
                print("       \(d.prefix(40).hex)...")
            } else {
                print("    (\(d.count)b): \(d.hex)")
            }
        }
        lastPrintedIdx = rxData.count
    }

    func finish() {
        print("\n  Total notifications: \(rxData.count)")
        print("Done!")
        DispatchQueue.main.asyncAfter(deadline: .now() + 1) { exit(0) }
    }

    // MARK: - Central delegate
    func centralManagerDidUpdateState(_ central: CBCentralManager) {
        guard central.state == .poweredOn else {
            print("BT state: \(central.state.rawValue)")
            return
        }
        let known = central.retrieveConnectedPeripherals(withServices: [TRUMA_SERVICE])
        if let t = known.first {
            print("Found bonded Truma")
            peripheral = t; t.delegate = self
            central.connect(t); return
        }
        print("Scanning...")
        central.scanForPeripherals(withServices: nil)
    }

    func centralManager(_ central: CBCentralManager, didDiscover p: CBPeripheral,
                         advertisementData: [String: Any], rssi: NSNumber) {
        guard peripheral == nil, let n = p.name, n.contains("iNet") || n.contains("Truma") else { return }
        print("Found: \(n) (RSSI: \(rssi))")
        central.stopScan()
        peripheral = p; p.delegate = self
        print("Connecting...")
        central.connect(p)
    }

    func centralManager(_ central: CBCentralManager, didConnect p: CBPeripheral) {
        print("Connected!")
        p.discoverServices(nil)
    }

    func centralManager(_ central: CBCentralManager, didFailToConnect p: CBPeripheral, error: Error?) {
        print("Connect failed: \(error?.localizedDescription ?? "?")")
        exit(1)
    }

    func centralManager(_ central: CBCentralManager, didDisconnectPeripheral p: CBPeripheral, error: Error?) {
        print("Disconnected: \(error?.localizedDescription ?? "clean")")
    }

    // MARK: - Peripheral delegate
    func peripheral(_ p: CBPeripheral, didDiscoverServices error: Error?) {
        for svc in p.services ?? [] {
            print("  Service: \(svc.uuid)")
            p.discoverCharacteristics(nil, for: svc)
        }
    }

    func peripheral(_ p: CBPeripheral, didDiscoverCharacteristicsFor svc: CBService, error: Error?) {
        for c in svc.characteristics ?? [] {
            chars[c.uuid] = c
            var props = ""
            if c.properties.contains(.read) { props += "r" }
            if c.properties.contains(.write) { props += "w" }
            if c.properties.contains(.writeWithoutResponse) { props += "W" }
            if c.properties.contains(.notify) { props += "n" }
            print("    \(c.uuid) [\(props)]")
            // Only subscribe to CMD and DATA_R — NOT CMD_ALT (per capture analysis)
            if c.properties.contains(.notify) && c.uuid != CHAR_CMD_ALT {
                // Force re-write CCCD: disable then enable to ensure it reaches the device
                p.setNotifyValue(false, for: c)
                DispatchQueue.main.asyncAfter(deadline: .now() + 0.5) {
                    p.setNotifyValue(true, for: c)
                }
            }
        }
    }

    func peripheral(_ p: CBPeripheral, didUpdateNotificationStateFor c: CBCharacteristic, error: Error?) {
        if let e = error {
            print("  Notify FAIL \(c.uuid): \(e.localizedDescription)")
            return
        }
        notifyCount += 1
        print("  Notify ON: \(c.uuid) (\(notifyCount)/3)")

        // Start tests once CMD + DATA_R + CMD_ALT notifications are all active
        if notifyCount >= 2 && !testStarted {
            testStarted = true
            print("\n  All notifications active. Starting tests in 3s...")
            testQueue.asyncAfter(deadline: .now() + 3) {
                self._runTests()
            }
        }
    }

    func peripheral(_ p: CBPeripheral, didUpdateValueFor c: CBCharacteristic, error: Error?) {
        guard let data = c.value else { return }
        let label = c.uuid == CHAR_CMD ? "CMD" : c.uuid == CHAR_DATA_R ? "DATA" : c.uuid == CHAR_CMD_ALT ? "CMD2" : "?"
        print("  RX \(label) (\(data.count)b): \(data.prefix(20).hex)\(data.count > 20 ? "..." : "")")
        rxData.append(data)
        notifySem.signal()
    }

    func peripheral(_ p: CBPeripheral, didWriteValueFor c: CBCharacteristic, error: Error?) {
        writeError = error
        if let e = error {
            print("  WRITE ERR \(c.uuid): code=\((e as NSError).code) \(e.localizedDescription)")
        }
        writeSem.signal()
    }
}

extension Data {
    var hex: String { map { String(format: "%02x", $0) }.joined() }
}

let test = TrumaTest()
dispatchMain()
