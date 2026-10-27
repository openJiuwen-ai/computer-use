import AppKit

/// Deliberately slow native callbacks, independent of any business application.
@MainActor enum ProbeLog {
    static let directory = Bundle.main.bundleURL.deletingLastPathComponent()
    static func record(_ control: String, _ phase: String, value: Any? = nil) {
        var event: [String: Any] = [
            "control": control, "phase": phase, "time": ProcessInfo.processInfo.systemUptime,
            "foreground": NSWorkspace.shared.frontmostApplication?.processIdentifier ?? 0
        ]
        if let value { event["value"] = value }
        let file = directory.appendingPathComponent("events.jsonl")
        if !FileManager.default.fileExists(atPath: file.path) {
            FileManager.default.createFile(atPath: file.path, contents: nil)
        }
        let handle = try! FileHandle(forWritingTo: file)
        try! handle.seekToEnd()
        try! handle.write(contentsOf: JSONSerialization.data(withJSONObject: event) + Data([10]))
        try! handle.close()
    }
}

@MainActor final class SlowButton: NSView {
    let controlID: String
    let delay: TimeInterval
    var presses = 0
    init(_ identifier: String, delay: TimeInterval, frame: NSRect) {
        self.controlID = identifier
        self.delay = delay
        super.init(frame: frame)
        setAccessibilityElement(true)
        setAccessibilityRole(.button)
        setAccessibilityLabel(identifier)
        setAccessibilityIdentifier(identifier)
    }
    required init?(coder: NSCoder) { fatalError() }
    override func accessibilityActionNames() -> [NSAccessibility.Action] { [.press] }
    override func accessibilityPerformAction(_ action: NSAccessibility.Action) {
        guard action == .press else { return }
        MainActor.assumeIsolated {
            ProbeLog.record(controlID, "entered")
            Thread.sleep(forTimeInterval: delay)
            presses += 1
            needsDisplay = true
            ProbeLog.record(controlID, "returned", value: presses)
        }
    }
    override func accessibilityValue() -> Any? { String(presses) }
    override func draw(_ rect: NSRect) {
        NSColor.controlBackgroundColor.setFill()
        bounds.fill()
        ("\(controlID): \(presses)" as NSString).draw(
            at: NSPoint(x: 8, y: 8), withAttributes: [.foregroundColor: NSColor.labelColor])
    }
}

@MainActor final class SlowField: NSTextField {
    override func setAccessibilityValue(_ value: Any?) {
        ProbeLog.record("slow-field", "entered", value: value)
        Thread.sleep(forTimeInterval: 0.45)
        if let text = value as? String { stringValue = text }
        ProbeLog.record("slow-field", "returned", value: stringValue)
    }
}

@MainActor final class SlowText: NSView {
    let text: String
    let alwaysSlow: Bool
    init(_ text: String, alwaysSlow: Bool = false, frame: NSRect) {
        self.text = text
        self.alwaysSlow = alwaysSlow
        super.init(frame: frame)
        setAccessibilityElement(true)
        setAccessibilityRole(.staticText)
        setAccessibilityIdentifier(text)
    }
    required init?(coder: NSCoder) { fatalError() }
    override func accessibilityValue() -> Any? {
        if alwaysSlow
            || FileManager.default.fileExists(atPath: ProbeLog.directory.appendingPathComponent("slow-read").path)
        {
            Thread.sleep(forTimeInterval: alwaysSlow ? 0.45 : 0.65)
        }
        return text
    }
    override func draw(_ rect: NSRect) {
        (text as NSString).draw(at: .zero, withAttributes: [.foregroundColor: NSColor.labelColor])
    }
}

@MainActor final class Probe: NSObject, NSApplicationDelegate {
    var window: NSWindow!
    func applicationDidFinishLaunching(_ notification: Notification) {
        window = NSWindow(
            contentRect: NSRect(x: 100, y: 120, width: 490, height: 590), styleMask: [.titled, .closable],
            backing: .buffered, defer: false)
        window.title = "AX timeout acceptance"
        window.tabbingMode = .disallowed
        for (index, id) in ["short-probe-press", "slow-press", "too-slow-press"].enumerated() {
            window.contentView!.addSubview(
                SlowButton(
                    id, delay: id == "too-slow-press" ? 2.6 : 0.45,
                    frame: NSRect(x: 20, y: 525 - index * 45, width: 440, height: 35)))
        }
        let field = SlowField(string: "original")
        field.frame = NSRect(x: 20, y: 385, width: 440, height: 30)
        field.setAccessibilityIdentifier("slow-field")
        window.contentView!.addSubview(field)
        window.contentView!.addSubview(
            SlowText("delayed-read-content", alwaysSlow: true, frame: NSRect(x: 20, y: 355, width: 440, height: 25)))
        let region = NSView(frame: NSRect(x: 20, y: 10, width: 440, height: 330))
        region.setAccessibilityElement(true)
        region.setAccessibilityRole(.group)
        region.setAccessibilityIdentifier("budget-region")
        for index in 0..<20 {
            region.addSubview(
                SlowText("budget-text-\(index)", frame: NSRect(x: 0, y: 310 - index * 16, width: 440, height: 16)))
        }
        window.contentView!.addSubview(region)
        window.orderBack(nil)
        try! String(ProcessInfo.processInfo.processIdentifier).write(
            to: ProbeLog.directory.appendingPathComponent("ready"), atomically: true, encoding: .utf8)
        Timer.scheduledTimer(withTimeInterval: 180, repeats: false) { _ in
            MainActor.assumeIsolated { NSApp.terminate(nil) }
        }
    }
}
let app = NSApplication.shared
let delegate = Probe()
app.delegate = delegate
app.setActivationPolicy(.accessory)
app.run()
