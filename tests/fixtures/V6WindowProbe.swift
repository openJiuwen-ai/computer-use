import AppKit

// Disposable independent documents, an owned sheet, and observable values.
// Timer output is a read-only test oracle; every tested input uses the SDK.
@MainActor final class V6WindowProbe: NSObject, NSApplicationDelegate {
    var first: NSWindow!
    var second: NSWindow!
    var sheet: NSPanel?
    var a = NSTextField(string: "root-A")
    var b = NSTextField(string: "root-B")
    var child = NSTextField(string: "child")
    var rootClosed = false
    var timer: Timer?
    let output = Bundle.main.bundleURL.deletingLastPathComponent().appendingPathComponent("state.json")

    func record() {
        let value: [String: Any] = [
            "a": a.stringValue, "b": b.stringValue, "child": child.stringValue, "sheet": sheet?.isVisible ?? false,
            "rootClosed": rootClosed
        ]
        try? JSONSerialization.data(withJSONObject: value, options: [.sortedKeys]).write(to: output, options: .atomic)
    }
    func button(_ title: String, _ action: Selector, _ x: CGFloat, in window: NSWindow) {
        let control = NSButton(title: title, target: self, action: action)
        control.frame = NSRect(x: x, y: 30, width: 170, height: 30)
        window.contentView!.addSubview(control)
    }
    func document(_ title: String, _ x: CGFloat, _ field: NSTextField, _ id: String) -> NSWindow {
        let window = NSWindow(
            contentRect: NSRect(x: x, y: 180, width: 460, height: 180), styleMask: [.titled, .closable],
            backing: .buffered, defer: false)
        window.title = title
        window.isReleasedWhenClosed = false
        field.frame = NSRect(x: 25, y: 100, width: 400, height: 26)
        field.setAccessibilityIdentifier(id)
        window.contentView!.addSubview(field)
        window.initialFirstResponder = field
        window.orderBack(nil)
        return window
    }
    func applicationDidFinishLaunching(_ notification: Notification) {
        first = document("V6 Document A", 120, a, "v6-field-a")
        second = document("V6 Document B", 650, b, "v6-field-b")
        button("Open owned dialog", #selector(openSheet), 20, in: first)
        button("Close document A", #selector(closeRoot), 220, in: first)
        first.makeKey()
        record()
        timer = Timer.scheduledTimer(withTimeInterval: 0.05, repeats: true) { [weak self] _ in
            MainActor.assumeIsolated { self?.record() }
        }
    }
    @objc func openSheet() {
        guard sheet == nil else { return }
        let panel = NSPanel(
            contentRect: NSRect(x: 0, y: 0, width: 360, height: 150), styleMask: [.titled], backing: .buffered,
            defer: false)
        panel.title = "V6 Save Dialog"
        child.frame = NSRect(x: 20, y: 85, width: 320, height: 26)
        child.setAccessibilityIdentifier("v6-child")
        panel.contentView!.addSubview(child)
        panel.initialFirstResponder = child
        button("Dismiss owned dialog", #selector(dismissSheet), 100, in: panel)
        sheet = panel
        first.beginSheet(panel) { _ in }
        record()
    }
    @objc func dismissSheet() {
        guard let panel = sheet else { return }
        first.endSheet(panel)
        panel.orderOut(nil)
        sheet = nil
        record()
    }
    @objc func closeRoot() {
        rootClosed = true
        first.close()
        record()
    }
}
let app = NSApplication.shared
app.setActivationPolicy(.regular)
let probe = V6WindowProbe()
app.delegate = probe
app.run()
