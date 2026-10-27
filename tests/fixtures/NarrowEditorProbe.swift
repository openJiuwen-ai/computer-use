import AppKit

// The AX text bounds describe a one-point caret when empty, while the actual
// editor is wide. Native focus alone does not initialize its editing session.
@MainActor final class NarrowEditor: NSTextView {
    var zeroGeometry = false
    override func accessibilityFrame() -> NSRect {
        var frame = super.accessibilityFrame()
        frame.size.width = zeroGeometry ? 0 : (string.isEmpty ? 1 : frame.width)
        return frame
    }
    override func setAccessibilityFocused(_ value: Bool) {}
}
@MainActor final class NarrowDelegate: NSObject, NSApplicationDelegate, NSTextViewDelegate {
    var window: NSWindow!
    let target = NarrowEditor(frame: NSRect(x: 25, y: 140, width: 360, height: 45))
    let unavailable = NarrowEditor(frame: NSRect(x: 25, y: 70, width: 360, height: 45))
    let untouched = NSTextField(string: "untouched")
    let root = Bundle.main.bundleURL.deletingLastPathComponent()
    func record() {
        let value: [String: Any] = [
            "target": target.string, "unavailable": unavailable.string, "untouched": untouched.stringValue
        ]
        try! JSONSerialization.data(withJSONObject: value, options: .sortedKeys).write(
            to: root.appendingPathComponent("values.json"), options: .atomic)
    }
    func textDidChange(_ notification: Notification) { record() }
    func applicationDidFinishLaunching(_ notification: Notification) {
        try! String(getpid()).write(to: root.appendingPathComponent("fixture.pid"), atomically: true, encoding: .utf8)
        window = NSWindow(
            contentRect: NSRect(x: 100, y: 110, width: 420, height: 240), styleMask: [.titled, .closable],
            backing: .buffered, defer: false)
        window.title = "Narrow editor probe"
        window.tabbingMode = .disallowed
        for (editor, identifier) in [(target, "narrow-editor"), (unavailable, "zero-editor")] {
            editor.isRichText = false
            editor.delegate = self
            editor.setAccessibilityIdentifier(identifier)
            window.contentView?.addSubview(editor)
        }
        unavailable.zeroGeometry = true
        untouched.frame = NSRect(x: 25, y: 20, width: 360, height: 25)
        untouched.setAccessibilityIdentifier("untouched")
        window.contentView?.addSubview(untouched)
        let menu = NSMenu(), edit = NSMenu(title: "Edit")
        edit.addItem(withTitle: "Paste", action: #selector(NSText.paste(_:)), keyEquivalent: "v")
        edit.addItem(withTitle: "Select All", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")
        let item = NSMenuItem(title: "Edit", action: nil, keyEquivalent: "")
        item.submenu = edit
        menu.addItem(item)
        NSApp.mainMenu = menu
        window.orderBack(nil)
        window.makeKey()
        window.makeFirstResponder(untouched)
        record()
    }
}
let app = NSApplication.shared
app.setActivationPolicy(.regular)
let delegate = NarrowDelegate()
app.delegate = delegate
app.run()
