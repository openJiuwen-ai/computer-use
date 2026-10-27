import AppKit

/// Legacy AX providers may repeat names. The real AX endpoint must still
/// address one action by name, not by the position of a catalogue entry.
@MainActor final class CatalogueControl: NSView {
    let kind: String
    weak var owner: Delegate?
    init(kind: String, frame: NSRect) {
        self.kind = kind
        super.init(frame: frame)
        setAccessibilityElement(true)
        setAccessibilityRole(.button)
        setAccessibilityLabel(kind)
        setAccessibilityIdentifier(kind)
    }
    required init?(coder: NSCoder) { fatalError() }
    override func acceptsFirstMouse(for event: NSEvent?) -> Bool { true }
    override func accessibilityActionNames() -> [NSAccessibility.Action] {
        switch kind {
        case "oversized": return (0...128).map { .init(rawValue: "Custom\($0)") }
        case "ambiguous": return [.increment, .decrement]
        default: return [.press, .press, .press]
        }
    }
    override func accessibilityActionDescription(_ action: NSAccessibility.Action) -> String? {
        kind == "ambiguous" ? "Same" : "Run \(action.rawValue)"
    }
    override func accessibilityPerformAction(_ action: NSAccessibility.Action) {
        MainActor.assumeIsolated { owner?.record(kind + ":" + action.rawValue) }
    }
    override func mouseDown(with event: NSEvent) { owner?.record(kind + ":pointer") }
    override func draw(_ dirtyRect: NSRect) {
        NSColor.controlBackgroundColor.setFill()
        dirtyRect.fill()
        (kind as NSString).draw(at: NSPoint(x: 10, y: 12), withAttributes: [.foregroundColor: NSColor.labelColor])
    }
}

@MainActor final class Delegate: NSObject, NSApplicationDelegate {
    let output = Bundle.main.bundleURL.deletingLastPathComponent().appendingPathComponent("state.json")
    var window: NSWindow!
    var counts: [String: Int] = [:]
    func record(_ name: String) {
        counts[name, default: 0] += 1
        publish()
    }
    func publish() {
        let point = NSEvent.mouseLocation
        let data: [String: Any] = [
            "pid": ProcessInfo.processInfo.processIdentifier, "counts": counts,
            "foreground": NSWorkspace.shared.frontmostApplication?.processIdentifier ?? 0, "pointer": [point.x, point.y]
        ]
        try! JSONSerialization.data(withJSONObject: data, options: [.sortedKeys]).write(to: output, options: .atomic)
    }
    func applicationDidFinishLaunching(_ notification: Notification) {
        window = NSWindow(
            contentRect: NSRect(x: 120, y: 120, width: 460, height: 230), styleMask: [.titled, .closable],
            backing: .buffered, defer: false)
        window.title = "Native Action Catalogue Probe"
        window.isReleasedWhenClosed = false
        for (index, kind) in ["duplicate", "ambiguous", "oversized"].enumerated() {
            let view = CatalogueControl(kind: kind, frame: NSRect(x: 30, y: 160 - index * 65, width: 400, height: 45))
            view.owner = self
            window.contentView!.addSubview(view)
        }
        window.orderBack(nil)
        publish()
        Timer.scheduledTimer(withTimeInterval: 0.05, repeats: true) { [weak self] _ in
            MainActor.assumeIsolated { self?.publish() }
        }
    }
}

let app = NSApplication.shared
let delegate = Delegate()
app.delegate = delegate
app.setActivationPolicy(.regular)
app.run()
