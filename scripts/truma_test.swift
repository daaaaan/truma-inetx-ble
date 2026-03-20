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

    var params: [String: Any] = [:] // topic.param -> value

    // MARK: - CBOR encoder
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
        else if b.count < 256 { d.append(0x78); d.append(UInt8(b.count)) }
        else { d.append(0x79); d.append(UInt8(b.count >> 8)); d.append(UInt8(b.count & 0xFF)) }
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

    // MARK: - CBOR decoder (minimal, handles Truma payloads)
    func cborDecode(_ data: Data) -> Any? {
        var offset = 0
        return cborDecodeValue(data, &offset)
    }

    func cborDecodeValue(_ data: Data, _ offset: inout Int) -> Any? {
        guard offset < data.count else { return nil }
        let initial = data[offset]
        let major = initial >> 5
        let info = initial & 0x1F
        offset += 1

        switch major {
        case 0: // unsigned int
            return cborDecodeUInt(info, data, &offset)
        case 1: // negative int
            if let u = cborDecodeUInt(info, data, &offset) { return -(Int(u) + 1) }
            return nil
        case 2: // byte string
            guard let len = cborDecodeUInt(info, data, &offset) else { return nil }
            let end = offset + Int(len)
            guard end <= data.count else { return nil }
            let result = data[offset..<end]
            offset = end
            return result
        case 3: // text string
            if info == 31 { // indefinite
                var s = ""
                while offset < data.count && data[offset] != 0xFF {
                    if let chunk = cborDecodeValue(data, &offset) as? String { s += chunk }
                }
                if offset < data.count { offset += 1 } // skip break
                return s
            }
            guard let len = cborDecodeUInt(info, data, &offset) else { return nil }
            let end = offset + Int(len)
            guard end <= data.count else { return nil }
            let s = String(data: data[offset..<end], encoding: .utf8) ?? ""
            offset = end
            return s
        case 4: // array
            if info == 31 { // indefinite
                var arr: [Any] = []
                while offset < data.count && data[offset] != 0xFF {
                    if let v = cborDecodeValue(data, &offset) { arr.append(v) }
                }
                if offset < data.count { offset += 1 }
                return arr
            }
            guard let len = cborDecodeUInt(info, data, &offset) else { return nil }
            var arr: [Any] = []
            for _ in 0..<Int(len) {
                if let v = cborDecodeValue(data, &offset) { arr.append(v) }
            }
            return arr
        case 5: // map
            if info == 31 { // indefinite map
                var dict: [(String, Any)] = []
                while offset < data.count && data[offset] != 0xFF {
                    let key = cborDecodeValue(data, &offset)
                    let val = cborDecodeValue(data, &offset)
                    if let k = key as? String, let v = val { dict.append((k, v)) }
                }
                if offset < data.count { offset += 1 }
                return dict
            }
            guard let len = cborDecodeUInt(info, data, &offset) else { return nil }
            var dict: [(String, Any)] = []
            for _ in 0..<Int(len) {
                let key = cborDecodeValue(data, &offset)
                let val = cborDecodeValue(data, &offset)
                if let k = key as? String, let v = val { dict.append((k, v)) }
            }
            return dict
        case 7: // simple/float
            if info == 20 { return false }
            if info == 21 { return true }
            if info == 22 { return "null" }
            if info == 31 { return "break" } // break
            return nil
        default:
            return nil
        }
    }

    func cborDecodeUInt(_ info: UInt8, _ data: Data, _ offset: inout Int) -> UInt64? {
        if info < 24 { return UInt64(info) }
        if info == 24 {
            guard offset < data.count else { return nil }
            let v = UInt64(data[offset]); offset += 1; return v
        }
        if info == 25 {
            guard offset + 2 <= data.count else { return nil }
            let v = UInt64(data[offset]) << 8 | UInt64(data[offset+1]); offset += 2; return v
        }
        if info == 26 {
            guard offset + 4 <= data.count else { return nil }
            let v = UInt64(data[offset]) << 24 | UInt64(data[offset+1]) << 16 |
                    UInt64(data[offset+2]) << 8 | UInt64(data[offset+3]); offset += 4; return v
        }
        return nil
    }

    // MARK: - Parse V3 frame and extract parameters
    func parseV3(_ data: Data) -> (String, String, Any?)? {
        guard data.count >= 18 else { return nil }
        let ctrl = data[6]
        guard ctrl == 0x03 else { return nil } // MBP only
        let subType = data[16]
        let cbor = data[18...]
        guard let decoded = cborDecode(Data(cbor)) else { return nil }

        // Extract tn/pn/v from decoded CBOR
        if let dict = decoded as? [(String, Any)] {
            var tn = "", pn = ""
            var value: Any?
            for (k, v) in dict {
                if k == "tn" { tn = "\(v)" }
                else if k == "pn" { pn = "\(v)" }
                else if k == "v" { value = v }
            }
            if !tn.isEmpty && !pn.isEmpty {
                return (tn, pn, value)
            }
        }
        return nil
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
        // Step 1: InitDataTransfer
        let announce = Data([0x01, UInt8(packet.count & 0xFF), UInt8(packet.count >> 8)])
        print("  TX CMD: \(announce.hex)  [InitDataTransfer sz=\(packet.count)]")

        if let err = writeCmd(announce) {
            print("  [CMD ERR] \(err.localizedDescription)")
            return false
        }

        // Step 2: Wait for Ready (0x81 0x00)
        guard let ready = waitNotify(timeout: 5) else {
            print("  [timeout waiting for Ready]")
            return false
        }
        print("  RX: \(ready.hex)")
        if ready.count >= 2 && ready[1] != 0x00 {
            print("  [NOT READY: status=0x\(String(format:"%02X", ready[1]))]")
            return false
        }

        // Step 3: Send data
        print("  TX DATA (\(packet.count)b): \(packet.prefix(30).hex)...")
        writeData(packet)

        // Step 4: Wait for AckDataTransfer (0xF0 0x01)
        guard let ack1 = waitNotify(timeout: 5) else {
            print("  [timeout waiting for DataAck]")
            return false
        }
        print("  RX: \(ack1.hex)")

        // Step 5: Wait for message ack (0x83 XX 0x00)
        guard let ack2 = waitNotify(timeout: 5) else {
            print("  [timeout waiting for MsgAck]")
            // Still OK — some messages don't get 0x83
            return true
        }
        print("  RX: \(ack2.hex)")

        // Step 6: Send confirm if we got 0x83
        if ack2.count > 0 && ack2[0] == 0x83 {
            print("  TX CMD: 0300  [confirm]")
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
        _runTransportTests()
    }


    func _runTransportTests() {
        // 1. Registration
        print("\n--- Registration ---")
        let regCbor = cborMap([("pv", [5, 1] as Any)])
        let regFrame = v3Frame(dest: 0xFFFF, src: 0x0500, ctrl: 0x01, sub: 0x01, corr: 0x42, cbor: regCbor)
        let _ = sendTransport(regFrame)
        Thread.sleep(forTimeInterval: 3) // wait for response + param discovery
        drainAndDecode()

        // 2. Subscribe — all topics in batches of 10 (per APK spec)
        print("\n--- Subscribe (all topics) ---")
        let batches: [[String]] = [
            ["AirCirculation", "AirCooling", "AirHeating", "DeviceManagement",
             "EnergySrc", "ErrorReset", "FreshWater", "GasBtl", "GasControl", "GreyWater"],
            ["Identify", "L1Bat", "L2Bat", "LinePower", "MobileIdentity",
             "PowerSupply", "RoomClimate", "Switches", "Temperature", "Transfer"],
            ["VBat", "WaterHeating", "AmbientLight", "Panel", "BatteryMngmt",
             "Install", "Connect", "TimerConfig", "BleDeviceManagement", "BluetoothDevice"],
            ["System", "Resources", "PowerMgmt"]
        ]
        for (i, batch) in batches.enumerated() {
            var subCbor = Data()
            subCbor.append(0xA1)
            cborStr("tn", &subCbor)
            if batch.count < 24 { subCbor.append(0x80 | UInt8(batch.count)) }
            else { subCbor.append(0x98); subCbor.append(UInt8(batch.count)) }
            for t in batch { cborStr(t, &subCbor) }
            let subFrame = v3Frame(dest: 0x0000, src: 0x0500, ctrl: 0x03, sub: 0x02, corr: 0, cbor: subCbor)
            print("  Batch \(i+1)/\(batches.count) (\(batch.count) topics)")
            let _ = sendTransport(subFrame)
            Thread.sleep(forTimeInterval: 0.5) // 250ms+ delay between batches
        }
        Thread.sleep(forTimeInterval: 3)
        drainAndDecode()

        // 3. SystemTime
        print("\n--- SystemTime ---")
        let ts = Int(Date().timeIntervalSince1970)
        let timeCbor = cborMap([("tn", "SystemTime" as Any), ("pn", "Time" as Any), ("v", ts as Any)])
        let timeFrame = v3Frame(dest: 0x0101, src: 0x0500, ctrl: 0x03, sub: 0x01, corr: 0, cbor: timeCbor)
        let _ = sendTransport(timeFrame)
        Thread.sleep(forTimeInterval: 0.5)

        let lotCbor = cborMap([("tn", "SystemTime" as Any), ("pn", "Lot" as Any), ("v", 0 as Any)])
        let lotFrame = v3Frame(dest: 0x0101, src: 0x0500, ctrl: 0x03, sub: 0x01, corr: 0, cbor: lotCbor)
        let _ = sendTransport(lotFrame)
        Thread.sleep(forTimeInterval: 1)

        // 4. MobileIdentity
        print("\n--- MobileIdentity ---")
        let idFields: [(String, String)] = [
            ("UserName", "Vanlin Controller"),
            ("Muid", "VANLIN-TEST-001"),
            ("Uuid", "vanlin-test-uuid-001")
        ]
        for (param, value) in idFields {
            let cbor = cborMap([("tn", "MobileIdentity" as Any), ("pn", param as Any), ("v", value as Any)])
            let frame = v3Frame(dest: 0x0101, src: 0x0500, ctrl: 0x03, sub: 0x01, corr: 0, cbor: cbor)
            let _ = sendTransport(frame)
            Thread.sleep(forTimeInterval: 0.3)
        }

        // 5. LastMessage marker
        let lastCbor = cborMap([("LastMessage", 1 as Any)])
        let lastFrame = v3Frame(dest: 0x0500, src: 0x0500, ctrl: 0x03, sub: 0x01, corr: 0, cbor: lastCbor)
        let _ = sendTransport(lastFrame)
        Thread.sleep(forTimeInterval: 2)
        drainAndDecode()

        // 6. Listen for live data
        print("\n--- Listening 30s for live data ---")
        for i in 0..<6 {
            Thread.sleep(forTimeInterval: 5)
            drainAndDecode()
            if i < 5 { print("  ... \((i+1)*5)s ...") }
        }

        // 7. Display collected parameters
        displayStatus()

        // 8. Prompt: send a command?
        print("\n--- Sending test command: Read AirHeating ---")
        // Send INFO request (read) for AirHeating
        let readCbor = cborMap([("tn", "AirHeating" as Any), ("pn", "TgtTemp" as Any), ("v", 0 as Any)])
        let readFrame = v3Frame(dest: 0x0101, src: 0x0500, ctrl: 0x03, sub: 0x01, corr: 0, cbor: readCbor)
        let _ = sendTransport(readFrame)
        Thread.sleep(forTimeInterval: 5)
        drainAndDecode()

        displayStatus()
        finish()
    }

    func drainAndDecode() {
        // Process all unprocessed V3 frames
        while lastPrintedIdx < rxData.count {
            let d = rxData[lastPrintedIdx]
            lastPrintedIdx += 1
            guard d.count > 18 else { continue }

            if let (tn, pn, val) = parseV3(d) {
                let key = "\(tn).\(pn)"
                params[key] = val
                // Format value for display
                var valStr = "\(val ?? "nil")"
                if let v = val as? UInt64 {
                    // Temperature check
                    if pn.contains("Temp") || pn.contains("Tgt") {
                        valStr = "\(v) (\(Double(v)/10.0)°C)"
                    } else {
                        valStr = "\(v)"
                    }
                }
                print("    \(key) = \(valStr)")
            }
        }
    }

    func displayStatus() {
        print("\n" + String(repeating: "=", count: 50))
        print("  TRUMA HEATER STATUS")
        print(String(repeating: "=", count: 50))

        let sections: [(String, [String])] = [
            ("ROOM CLIMATE", ["RoomClimate.Active", "RoomClimate.Mode", "RoomClimate.TgtTemp"]),
            ("AIR HEATING", ["AirHeating.Active", "AirHeating.Temp", "AirHeating.TgtTemp",
                             "AirHeating.Mode", "AirHeating.FanLevel"]),
            ("WATER HEATING", ["WaterHeating.Active", "WaterHeating.Mode", "WaterHeating.Temp"]),
            ("ENERGY", ["EnergySrc.GasLevel", "EnergySrc.ElectricLevel", "EnergySrc.DieselLevel"]),
            ("POWER", ["PowerSupply.DPlus", "PowerSupply.MainSwitch"]),
        ]

        let modeNames: [Int: String] = [0: "OFF", 1: "ACC", 2: "COOLING", 3: "HEATING",
                                          4: "HEATING_AC", 5: "VENTING", 6: "DEHUMIDIFYING"]
        let activeNames: [Int: String] = [0: "OFF", 1: "ACTIVE", 2: "IDLE"]

        for (section, keys) in sections {
            print("\n  [\(section)]")
            for key in keys {
                if let val = params[key] {
                    var display = "\(val)"
                    let intVal = (val as? UInt64).map { Int($0) } ?? (val as? Int)
                    if key.hasSuffix(".Mode"), let iv = intVal { display = modeNames[iv] ?? "\(iv)" }
                    if key.hasSuffix(".Active"), let iv = intVal { display = activeNames[iv] ?? "\(iv)" }
                    if key.contains("Temp"), let iv = intVal { display = "\(Double(iv)/10.0)°C" }
                    let shortKey = key.components(separatedBy: ".").last ?? key
                    print("    \(shortKey): \(display)")
                } else {
                    let shortKey = key.components(separatedBy: ".").last ?? key
                    print("    \(shortKey): --")
                }
            }
        }

        // Show all other received parameters
        let knownKeys = Set(sections.flatMap { $0.1 })
        let otherKeys = params.keys.filter { !knownKeys.contains($0) }.sorted()
        if !otherKeys.isEmpty {
            print("\n  [OTHER PARAMETERS]")
            for key in otherKeys {
                print("    \(key) = \(params[key] ?? "nil")")
            }
        }
        print(String(repeating: "=", count: 50))
    }

    var lastPrintedIdx = 0

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

        // Handle inbound transport: when Truma sends data on DATA_R, ACK it
        if c.uuid == CHAR_DATA_R && data.count > 4 {
            print("  -> Incoming data! Sending ACK f001")
            if let cmdChar = chars[CHAR_CMD] {
                p.writeValue(Data([0xF0, 0x01]), for: cmdChar, type: .withResponse)
            }
        }

        // When we receive 0x83 (message ack from Truma), send 0x03 0x00 confirm
        if c.uuid == CHAR_CMD && data.count >= 2 && data[0] == 0x83 {
            print("  -> MsgAck received, sending confirm 0300")
            if let cmdChar = chars[CHAR_CMD] {
                p.writeValue(Data([0x03, 0x00]), for: cmdChar, type: .withResponse)
            }
        }

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
