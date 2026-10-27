import AppKit
import Carbon

/// A deliberately isolated recipient for attribution experiments. It observes
/// only events delivered to this process; it never posts an event, activates
/// an application, or reads any other application's text or keyboard input.
@MainActor final class InputCanaryDelegate: NSObject, NSApplicationDelegate, NSTextViewDelegate, NSWindowDelegate {
    // The public Process Manager exports remain available in C but are not
    // imported into modern Swift. Resolve optionally for this diagnostic only;
    // unavailable exports stay unknown instead of borrowing NSApp.isActive.
    private typealias ReadFront = @convention(c) (UnsafeMutablePointer<ProcessSerialNumber>) -> OSErr
    private typealias ReadPID =
        @convention(c) (UnsafePointer<ProcessSerialNumber>, UnsafeMutablePointer<pid_t>) -> OSStatus
    private static let processLibrary = dlopen(
        "/System/Library/Frameworks/ApplicationServices.framework/Frameworks/HIServices.framework/HIServices", RTLD_LAZY
    )
    private static let readFront: ReadFront? = processLibrary.flatMap { handle in
        dlsym(handle, "GetFrontProcess").map { unsafeBitCast($0, to: ReadFront.self) }
    }
    private static let readPID: ReadPID? = processLibrary.flatMap { handle in
        dlsym(handle, "GetProcessPID").map { unsafeBitCast($0, to: ReadPID.self) }
    }
    static let initialBody = "CANARY-UNCHANGED"
    static let initialGuard = "CANARY-GUARD"
    let directory: URL
    let lifetime: TimeInterval
    let started = ProcessInfo.processInfo.systemUptime
    var window: NSWindow!
    let editor = NSTextView()
    let guardField = NSTextField(labelWithString: initialGuard)
    var monitor: Any?
    var timer: Timer?
    var lastControlSequence: UInt64 = 0
    var eventSequence: UInt64 = 0
    var textSequence: UInt64 = 0
    var keyEvents = [[String: Any]]()
    var textChanges = [[String: Any]]()
    var frontChanges = [[String: Any]]()
    var controls = [[String: Any]]()
    var lastFrontSignature = ""
    var controlError: String?
    var totalKeys = 0
    var totalTextChanges = 0

    init(directory: URL, lifetime: TimeInterval) {
        self.directory = directory
        self.lifetime = lifetime
    }

    private func now() -> Double { ProcessInfo.processInfo.systemUptime }

    /// Process Manager and NSWorkspace are independent global observations;
    /// neither is inferred from the synthetic local NSApp.isActive state.
    private func frontState() -> [String: Any] {
        var serial = ProcessSerialNumber(highLongOfPSN: 0, lowLongOfPSN: 0)
        let frontStatus = Self.readFront?(&serial) ?? OSErr(-4)
        var pid: pid_t = 0
        let pidStatus = frontStatus == noErr ? Self.readPID?(&serial, &pid) ?? OSStatus(-4) : OSStatus(frontStatus)
        let workspacePID = NSWorkspace.shared.frontmostApplication?.processIdentifier
        return [
            "processManagerFrontPID": frontStatus == noErr && pidStatus == noErr ? pid : NSNull(),
            "processManagerFrontStatus": Int(frontStatus), "processManagerPIDStatus": Int(pidStatus),
            "processManagerFrontPSN": [serial.highLongOfPSN, serial.lowLongOfPSN],
            "frontPIDSource":
                "optional public C Process Manager GetFrontProcess + GetProcessPID; NSWorkspace separately",
            "workspaceFrontPID": workspacePID as Any? ?? NSNull()
        ]
    }

    private func responder() -> [String: Any] {
        guard let value = window?.firstResponder else { return ["kind": "none", "isBody": false] }
        return [
            "kind": String(describing: type(of: value)), "object": String(describing: ObjectIdentifier(value)),
            "isBody": value === editor
        ]
    }

    private func appendBounded(_ value: [String: Any], to values: inout [[String: Any]], limit: Int) {
        values.append(value)
        if values.count > limit { values.removeFirst(values.count - limit) }
    }

    private func sampleFront() -> [String: Any] {
        let front = frontState()
        let signature = """
            \(front["processManagerFrontPID"]!)|\(front["workspaceFrontPID"]!)|\(NSApp.isActive)|\
            \(NSApp.keyWindow?.windowNumber ?? 0)|\(NSApp.mainWindow?.windowNumber ?? 0)
            """
        if signature != lastFrontSignature {
            lastFrontSignature = signature
            var change = front
            change["uptime"] = now()
            change["active"] = NSApp.isActive
            change["keyWindow"] = NSApp.keyWindow?.windowNumber ?? 0
            change["mainWindow"] = NSApp.mainWindow?.windowNumber ?? 0
            appendBounded(change, to: &frontChanges, limit: 80)
        }
        return front
    }

    func record(_ reason: String) {
        guard let window else { return }
        let value: [String: Any] = [
            "schema": 1, "pid": getpid(), "nativeWindow": window.windowNumber, "uptime": now(),
            "startedUptime": started, "lifetimeSeconds": lifetime, "reason": reason, "active": NSApp.isActive,
            "isKey": window.isKeyWindow, "isMain": window.isMainWindow, "visible": window.isVisible,
            "keyWindow": NSApp.keyWindow?.windowNumber ?? 0, "mainWindow": NSApp.mainWindow?.windowNumber ?? 0,
            "front": sampleFront(), "firstResponder": responder(), "body": editor.string,
            "bodyUTF16": Array(editor.string.utf16).map(Int.init),
            "selection": [editor.selectedRange().location, editor.selectedRange().length],
            "guard": guardField.stringValue, "controlSequence": lastControlSequence,
            "controlError": controlError as Any? ?? NSNull(), "controlHistory": controls,
            "keyEventSequence": eventSequence, "textChangeSequence": textSequence,
            "totalKeyEventsSinceReset": totalKeys, "totalTextChangesSinceReset": totalTextChanges,
            "keyEvents": keyEvents, "textChanges": textChanges, "frontChanges": frontChanges,
            "observation": "local NSEvent keyDown/keyUp only; no global keyboard monitor or event posting"
        ]
        do {
            let data = try JSONSerialization.data(withJSONObject: value, options: [.sortedKeys, .prettyPrinted])
            try data.write(to: directory.appendingPathComponent("state.json"), options: .atomic)
        } catch {
            // Diagnostics must not alter input or take focus when persistence fails.
            FileHandle.standardError.write(Data("canary state write failed: \(error)\n".utf8))
        }
    }

    private func receive(_ event: NSEvent) {
        guard event.type == .keyDown || event.type == .keyUp else { return }
        eventSequence += 1
        totalKeys += 1
        var value: [String: Any] = [
            "sequence": eventSequence, "uptime": now(), "eventTimestamp": event.timestamp,
            "type": event.type == .keyDown ? "keyDown" : "keyUp", "keyCode": event.keyCode,
            "flags": event.modifierFlags.rawValue, "characters": event.characters as Any? ?? NSNull(),
            "charactersIgnoringModifiers": event.charactersIgnoringModifiers as Any? ?? NSNull(),
            "charactersUTF16": event.characters.map { Array($0.utf16).map(Int.init) } as Any? ?? NSNull(),
            "eventWindowID": event.windowNumber, "ourNativeWindowID": window.windowNumber,
            "eventWindowIsOurWindow": event.window === window, "firstResponder": responder(), "active": NSApp.isActive,
            "front": sampleFront(), "bodyBeforeDelivery": editor.string
        ]
        if let cg = event.cgEvent {
            var actual = 0
            cg.keyboardGetUnicodeString(maxStringLength: 0, actualStringLength: &actual, unicodeString: nil)
            var units = [UniChar](repeating: 0, count: min(actual, 4096))
            var copied = 0
            units.withUnsafeMutableBufferPointer { buffer in
                cg.keyboardGetUnicodeString(
                    maxStringLength: buffer.count, actualStringLength: &copied, unicodeString: buffer.baseAddress)
            }
            value["cgEventUnicodeUTF16"] = units.prefix(min(copied, units.count)).map(Int.init)
            value["cgEventUnicodeReportedUnits"] = actual
            value["cgEventUnicodeTruncated"] = actual > units.count
            value["sourcePID"] = cg.getIntegerValueField(.eventSourceUnixProcessID)
            value["targetPID"] = cg.getIntegerValueField(.eventTargetUnixProcessID)
            value["cgWindowFields"] = [
                "51": cg.getIntegerValueField(CGEventField(rawValue: 51)!),
                "91": cg.getIntegerValueField(CGEventField(rawValue: 91)!),
                "92": cg.getIntegerValueField(CGEventField(rawValue: 92)!)
            ]
        }
        appendBounded(value, to: &keyEvents, limit: 512)
        record("local-event-before-AppKit-delivery")
    }

    func textDidChange(_ notification: Notification) {
        guard notification.object as AnyObject? === editor else { return }
        textSequence += 1
        totalTextChanges += 1
        appendBounded(
            [
                "sequence": textSequence, "uptime": now(), "body": editor.string,
                "bodyUTF16": Array(editor.string.utf16).map(Int.init), "firstResponder": responder(),
                "front": sampleFront()
            ], to: &textChanges, limit: 256)
        record("body-textDidChange")
    }

    /// Out-of-band reset is explicitly logged, uses no keyboard, and never
    /// changes application/window focus. Stale or replayed sequences do nothing.
    private func pollControl() {
        let path = directory.appendingPathComponent("control.json")
        guard let data = try? Data(contentsOf: path),
            let command = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
            let number = command["seq"] as? NSNumber, CFGetTypeID(number) != CFBooleanGetTypeID(),
            number.doubleValue.isFinite, number.doubleValue >= 1,
            number.doubleValue.rounded(.towardZero) == number.doubleValue
        else { return }
        let seq = number.uint64Value
        guard seq > lastControlSequence else { return }
        lastControlSequence = seq
        guard command["action"] as? String == "reset" else {
            controlError = "unsupported action; only reset is accepted"
            record("control-rejected")
            return
        }
        controlError = nil
        editor.string = Self.initialBody
        editor.setSelectedRange(NSRange(location: Self.initialBody.utf16.count, length: 0))
        editor.undoManager?.removeAllActions()
        keyEvents.removeAll()
        textChanges.removeAll()
        totalKeys = 0
        totalTextChanges = 0
        appendBounded(
            [
                "seq": seq, "action": "reset", "uptime": now(), "body": editor.string, "guard": guardField.stringValue,
                "focusUnchanged": true
            ], to: &controls, limit: 24)
        record("programmatic-control-reset-no-input")
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        window = NSWindow(
            contentRect: NSRect(x: 240, y: 280, width: 620, height: 360),
            styleMask: [.titled, .closable, .miniaturizable, .resizable], backing: .buffered, defer: false)
        window.title = "Input Canary — isolated attribution"
        window.isReleasedWhenClosed = false
        window.tabbingMode = .disallowed
        window.delegate = self
        let note = NSTextField(
            labelWithString: "Owned test recipient. Activate manually; it records only its own delivered keys.")
        note.frame = NSRect(x: 20, y: 316, width: 580, height: 26)
        window.contentView!.addSubview(note)
        let scroll = NSScrollView(frame: NSRect(x: 20, y: 85, width: 580, height: 225))
        scroll.autoresizingMask = [.width, .height]
        scroll.hasVerticalScroller = true
        scroll.borderType = .bezelBorder
        editor.frame = scroll.contentView.bounds
        editor.autoresizingMask = [.width]
        editor.isRichText = false
        editor.isEditable = true
        editor.isSelectable = true
        editor.isAutomaticQuoteSubstitutionEnabled = false
        editor.isAutomaticDashSubstitutionEnabled = false
        editor.isAutomaticTextReplacementEnabled = false
        editor.isContinuousSpellCheckingEnabled = false
        editor.font = NSFont.monospacedSystemFont(ofSize: 15, weight: .regular)
        editor.string = Self.initialBody
        editor.delegate = self
        editor.setAccessibilityIdentifier("canary-body")
        editor.setAccessibilityLabel("Canary body")
        scroll.documentView = editor
        window.contentView!.addSubview(scroll)
        guardField.frame = NSRect(x: 20, y: 35, width: 580, height: 30)
        guardField.setAccessibilityIdentifier("canary-readonly-guard")
        guardField.setAccessibilityLabel("Canary readonly guard")
        window.contentView!.addSubview(guardField)
        window.makeFirstResponder(editor)
        editor.setSelectedRange(NSRange(location: Self.initialBody.utf16.count, length: 0))
        monitor = NSEvent.addLocalMonitorForEvents(matching: [.keyDown, .keyUp]) { [weak self] event in
            self?.receive(event)
            return event
        }
        // Show without activation or becoming key. A owns explicit foreground setup.
        window.orderFront(nil)
        record("ready-no-activation")
        timer = Timer.scheduledTimer(withTimeInterval: 0.05, repeats: true) { [weak self] _ in
            MainActor.assumeIsolated {
                guard let self else { return }
                self.pollControl()
                self.record("tick")
                if self.now() - self.started >= self.lifetime {
                    self.record("lifetime-ended")
                    NSApp.terminate(nil)
                }
            }
        }
    }

    func windowWillClose(_ notification: Notification) {
        record("window-closed")
        NSApp.terminate(nil)
    }
    func applicationWillTerminate(_ notification: Notification) {
        timer?.invalidate()
        if let monitor { NSEvent.removeMonitor(monitor) }
        record("terminated")
    }
}

guard CommandLine.arguments.count >= 2 else {
    FileHandle.standardError.write(Data("usage: InputCanaryProbe output-directory [lifetime-seconds]\n".utf8))
    exit(2)
}
let directory = URL(fileURLWithPath: CommandLine.arguments[1], isDirectory: true)
try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
let application = NSApplication.shared
application.setActivationPolicy(.regular)
let delegate = InputCanaryDelegate(
    directory: directory, lifetime: CommandLine.arguments.count > 2 ? Double(CommandLine.arguments[2]) ?? 900 : 900)
application.delegate = delegate
application.run()
