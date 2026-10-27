import AppKit

// Real AppKit responder-chain fixture. The application reports the actual
// key window's first responder and uses ordinary NSApplication event dispatch.
@MainActor final class ContextApplication: NSApplication {
    override func accessibilityApplicationFocusedUIElement() -> Any? {
        if let input = keyWindow?.firstResponder as? ContextInput { return input }
        return super.accessibilityApplicationFocusedUIElement()
    }
    override func sendEvent(_ event: NSEvent) {
        if [.keyDown, .keyUp].contains(event.type) {
            (delegate as? ContextDelegate)?.record(
                "key-event",
                extra: ["type": event.type.rawValue, "eventWindow": event.windowNumber, "keyCode": event.keyCode])
        }
        super.sendEvent(event)
    }
}

@MainActor final class ContextWindow: NSWindow {
    weak var recorder: ContextDelegate?
    var primary: ContextInput?
    override func setAccessibilityMain(_ value: Bool) {
        super.setAccessibilityMain(value)
        if value, let primary {
            makeKey()
            makeFirstResponder(primary)
            recorder?.record("AXMain", extra: ["receiver": title])
        }
    }
}

@MainActor final class ContextInput: NSView {
    let name: String
    var text = "", returns = 0
    weak var recorder: ContextDelegate?
    init(_ name: String, frame: NSRect) {
        self.name = name
        super.init(frame: frame)
        setAccessibilityElement(true)
        setAccessibilityIdentifier(name)
        setAccessibilityLabel(name)
        setAccessibilityRole(.textField)
    }
    required init?(coder: NSCoder) { fatalError("init(coder:) not implemented") }
    override var acceptsFirstResponder: Bool { true }
    override func isAccessibilityFocused() -> Bool { window?.firstResponder === self }
    override func setAccessibilityFocused(_ value: Bool) {
        if value {
            window?.makeKey()
            window?.makeFirstResponder(self)
            recorder?.record("AXFocus", extra: ["receiver": name])
        }
    }
    override func accessibilityValue() -> Any? { text }
    override func setAccessibilityValue(_ value: Any?) {
        guard let value = value as? String else { return }
        text = value
        needsDisplay = true
        recorder?.record("AXValue", extra: ["receiver": name])
    }
    override func keyDown(with event: NSEvent) {
        if event.keyCode == 36 { returns += 1 } else { text += event.characters ?? "" }
        needsDisplay = true
        recorder?.record("input", extra: ["receiver": name])
    }
    override func draw(_ dirtyRect: NSRect) {
        NSColor.controlBackgroundColor.setFill()
        bounds.fill()
        ("\(name): \(text) / Return \(returns)" as NSString).draw(at: NSPoint(x: 6, y: 8))
    }
}

@MainActor final class ContextPad: NSView {
    weak var recorder: ContextDelegate?
    override var acceptsFirstResponder: Bool { true }
    override func acceptsFirstMouse(for event: NSEvent?) -> Bool { true }
    override func mouseDown(with event: NSEvent) {
        recorder?.record("mouse-down", extra: ["eventWindow": event.windowNumber])
    }
    override func mouseUp(with event: NSEvent) {
        // A real pointer action in A intentionally leaves B as the input document.
        recorder?.selectOther()
        recorder?.record("mouse-up", extra: ["eventWindow": event.windowNumber])
    }
    override func draw(_ dirtyRect: NSRect) {
        NSColor.systemGreen.withAlphaComponent(0.25).setFill()
        bounds.fill()
        ("Click here: leave focus in document B" as NSString).draw(at: NSPoint(x: 8, y: 14))
    }
}

@MainActor final class ContextDelegate: NSObject, NSApplicationDelegate {
    var a: ContextWindow!, b: ContextWindow!
    let first = ContextInput("A-primary", frame: NSRect(x: 20, y: 155, width: 350, height: 35))
    let alternate = ContextInput("A-alternate", frame: NSRect(x: 20, y: 110, width: 350, height: 35))
    let other = ContextInput("B-primary", frame: NSRect(x: 20, y: 155, width: 350, height: 35))
    var directory: URL { Bundle.main.bundleURL.deletingLastPathComponent() }
    func record(_ kind: String, extra: [String: Any] = [:]) {
        let inputs = [first, alternate, other]
        var record: [String: Any] = [
            "kind": kind, "timestampMs": Date().timeIntervalSince1970 * 1000, "keyWindow": NSApp.keyWindow?.title ?? "",
            "mainWindow": NSApp.mainWindow?.title ?? "",
            "focus": (NSApp.keyWindow?.firstResponder as? ContextInput)?.name ?? "",
            "foregroundPID": NSWorkspace.shared.frontmostApplication?.processIdentifier ?? 0,
            "returns": Dictionary(uniqueKeysWithValues: inputs.map { ($0.name, $0.returns) }),
            "values": Dictionary(uniqueKeysWithValues: inputs.map { ($0.name, $0.text) }),
            "windows": ["A": a?.windowNumber ?? 0, "B": b?.windowNumber ?? 0]
        ]
        record.merge(extra) { _, new in new }
        let file = directory.appendingPathComponent("events.jsonl")
        if !FileManager.default.fileExists(atPath: file.path) {
            FileManager.default.createFile(atPath: file.path, contents: nil)
        }
        let handle = try! FileHandle(forWritingTo: file)
        try! handle.seekToEnd()
        try! handle.write(
            contentsOf: JSONSerialization.data(withJSONObject: record, options: [.sortedKeys]) + Data([10]))
        try! handle.close()
    }
    func selectOther() {
        b.makeKey()
        b.makeMain()
        b.makeFirstResponder(other)
    }
    @objc func redirectWithinA() {
        a.makeKey()
        a.makeMain()
        a.makeFirstResponder(alternate)
        record("redirect-within-A")
    }
    func applicationDidFinishLaunching(_ notification: Notification) {
        try! String(getpid()).write(
            to: directory.appendingPathComponent("fixture.pid"), atomically: true, encoding: .utf8)
        func window(_ title: String, x: Double) -> ContextWindow {
            let value = ContextWindow(
                contentRect: NSRect(x: x, y: 170, width: 390, height: 220), styleMask: [.titled, .closable],
                backing: .buffered, defer: false)
            value.title = title
            value.isReleasedWhenClosed = false
            value.tabbingMode = .disallowed
            value.recorder = self
            value.orderBack(nil)
            return value
        }
        a = window("Cache document A", x: 100)
        b = window("Cache document B", x: 510)
        for input in [first, alternate, other] { input.recorder = self }
        a.contentView?.addSubview(first)
        a.contentView?.addSubview(alternate)
        b.contentView?.addSubview(other)
        a.primary = first
        b.primary = other
        let pad = ContextPad(frame: NSRect(x: 20, y: 55, width: 350, height: 45))
        pad.setAccessibilityElement(true)
        pad.setAccessibilityRole(.group)
        pad.setAccessibilityIdentifier("cross-window-pad")
        pad.setAccessibilityLabel("Leave focus in B")
        pad.recorder = self
        a.contentView?.addSubview(pad)
        let redirect = NSButton(title: "Redirect within A", target: self, action: #selector(redirectWithinA))
        redirect.setAccessibilityIdentifier("redirect-within-A")
        redirect.frame = NSRect(x: 20, y: 15, width: 200, height: 28)
        a.contentView?.addSubview(redirect)
        a.makeKey()
        a.makeMain()
        a.makeFirstResponder(first)
        record("ready")
        Timer.scheduledTimer(withTimeInterval: 180, repeats: false) { _ in
            MainActor.assumeIsolated { NSApp.terminate(nil) }
        }
    }
}
let application = ContextApplication.shared
application.setActivationPolicy(.regular)
let delegate = ContextDelegate()
application.delegate = delegate
application.run()
