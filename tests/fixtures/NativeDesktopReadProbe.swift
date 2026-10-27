import AppKit

/// Deliberately drawn pixels: neither content view exposes semantic children.
/// WindowServer must still report both the document and its untitled panel.
@MainActor final class Canvas: NSView {
    let label: String
    let fill: NSColor
    init(label: String, fill: NSColor, frame: NSRect) {
        self.label = label
        self.fill = fill
        super.init(frame: frame)
        setAccessibilityElement(false)
    }
    required init?(coder: NSCoder) { fatalError("init(coder:) is not supported") }
    override func accessibilityChildren() -> [Any]? { [] }
    override func draw(_ dirtyRect: NSRect) {
        fill.setFill()
        bounds.fill()
        (label as NSString).draw(
            at: NSPoint(x: 22, y: bounds.height / 2),
            withAttributes: [.font: NSFont.systemFont(ofSize: 24, weight: .bold), .foregroundColor: NSColor.white])
    }
}

@MainActor final class Delegate: NSObject, NSApplicationDelegate {
    var windows = [NSWindow]()
    func applicationDidFinishLaunching(_ notification: Notification) {
        let document = NSWindow(
            contentRect: NSRect(x: 160, y: 180, width: 460, height: 260), styleMask: [.titled, .closable],
            backing: .buffered, defer: false)
        document.title = "Desktop Read Probe"
        document.contentView = Canvas(
            label: "VISUAL DOCUMENT", fill: .systemBlue, frame: NSRect(x: 0, y: 0, width: 460, height: 260))
        let panel = NSPanel(
            contentRect: NSRect(x: 440, y: 210, width: 320, height: 140), styleMask: [.borderless, .nonactivatingPanel],
            backing: .buffered, defer: false)
        panel.title = ""
        panel.hidesOnDeactivate = false
        panel.contentView = Canvas(
            label: "VISUAL PANEL", fill: .systemOrange, frame: NSRect(x: 0, y: 0, width: 320, height: 140))
        windows = [document, panel]
        if CommandLine.arguments.contains("--background") {
            document.orderBack(nil)
            panel.orderBack(nil)
        } else {
            document.orderFrontRegardless()
            panel.orderFrontRegardless()
        }
        let output = URL(fileURLWithPath: Bundle.main.bundlePath).deletingLastPathComponent()
        try! JSONSerialization.data(withJSONObject: [
            "pid": ProcessInfo.processInfo.processIdentifier, "windows": windows.map(\.windowNumber)
        ]).write(to: output.appendingPathComponent("state.json"), options: .atomic)
    }
}

let app = NSApplication.shared
app.setActivationPolicy(.regular)
let delegate = Delegate()
app.delegate = delegate
app.run()
