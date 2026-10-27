import AppKit

/// A provider can expose a complete but empty subtree without a busy flag.
/// The independent pixels/state distinguish delayed, activation-only and truly
/// blank content. Only the harness-owned mode file changes these scenarios.
@MainActor final class ShellRegion: NSView {
    let output = Bundle.main.bundleURL.deletingLastPathComponent()
    let label = NSTextField(labelWithString: "Visible body from the native window")
    var mode = "content"
    var published = true
    var scheduled = false
    var childReads = 0
    var valueReads = 0

    override init(frame: NSRect) {
        super.init(frame: frame)
        setAccessibilityElement(true)
        setAccessibilityRole(.group)
        setAccessibilityIdentifier("shell-region")
        label.frame = NSRect(x: 12, y: 35, width: 410, height: 30)
        label.setAccessibilityIdentifier("body-label")
        addSubview(label)
    }
    required init?(coder: NSCoder) { fatalError("not used") }

    private func synchronize() {
        let requested = (try? String(contentsOf: output.appendingPathComponent("mode"), encoding: .utf8)) ?? "content"
        if requested != mode {
            mode = requested
            published = mode == "content"
            scheduled = false
            childReads = 0
            valueReads = 0
            label.isHidden = mode == "blank"
        }
        if mode == "activation" && NSApp.isActive { published = true }
    }
    private func record() {
        let facts: [String: Any] = [
            "mode": mode, "published": published, "active": NSApp.isActive, "childReads": childReads,
            "valueReads": valueReads, "pixelsHaveContent": !label.isHidden
        ]
        try! JSONSerialization.data(withJSONObject: facts).write(
            to: output.appendingPathComponent("state.json"), options: .atomic)
    }
    override func accessibilityValue() -> Any? {
        synchronize()
        valueReads += 1
        // Value is collected by the full fact reader, not window discovery.
        // There is deliberately no AXElementBusy or new provider-init receipt.
        if mode == "delayed" && !scheduled {
            scheduled = true
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.15) { [weak self] in
                guard let self, self.mode == "delayed" else { return }
                self.published = true
                self.record()
                NSAccessibility.post(element: self, notification: .layoutChanged)
            }
        }
        record()
        return nil
    }
    override func accessibilityChildren() -> [Any]? {
        synchronize()
        childReads += 1
        record()
        return published ? NSAccessibility.unignoredChildren(from: [label]) : []
    }
}

@MainActor final class Delegate: NSObject, NSApplicationDelegate {
    var window: NSWindow!
    func applicationDidFinishLaunching(_ notification: Notification) {
        window = NSWindow(
            contentRect: NSRect(x: 180, y: 240, width: 480, height: 190), styleMask: [.titled, .closable],
            backing: .buffered, defer: false)
        window.title = "Window Shell Probe"
        window.contentView!.addSubview(ShellRegion(frame: NSRect(x: 20, y: 30, width: 440, height: 120)))
        window.orderBack(nil)
        try! Data("ready".utf8).write(
            to: Bundle.main.bundleURL.deletingLastPathComponent().appendingPathComponent("ready"))
        Timer.scheduledTimer(withTimeInterval: 120, repeats: false) { _ in
            MainActor.assumeIsolated { NSApp.terminate(nil) }
        }
    }
}

let app = NSApplication.shared
let delegate = Delegate()
app.delegate = delegate
app.setActivationPolicy(.regular)
app.run()
