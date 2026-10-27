import AppKit

@MainActor final class ProbeApplication: NSApplication {
    override func accessibilityApplicationFocusedUIElement() -> Any? {
        guard let probe = delegate as? ProbeDelegate else { return super.accessibilityApplicationFocusedUIElement() }
        return probe.mode() == "other-window" ? probe.external : probe.other
    }
}
@MainActor final class ProbeEditor: NSTextView {
    var refuse = false
    var handoff = false
    var shadowRange: NSRange?
    override func accessibilitySelectedTextRange() -> NSRange { shadowRange ?? super.accessibilitySelectedTextRange() }
    override func setAccessibilitySelectedTextRange(_ range: NSRange) {
        if (NSApp.delegate as? ProbeDelegate)?.mode() == "shadow-selection" {
            shadowRange = range
        } else {
            super.setAccessibilitySelectedTextRange(range)
        }
    }
    override func selectAll(_ sender: Any?) {
        shadowRange = nil
        super.selectAll(sender)
    }
    override var acceptsFirstResponder: Bool { !refuse }
    override func setAccessibilityFocused(_ value: Bool) { /* Pointer preparation required. */  }
    override func isAccessibilityFocused() -> Bool {
        if let probe = NSApp.delegate as? ProbeDelegate, probe.mode() == "paragraph", self === probe.target {
            return false
        }
        return refuse || window?.firstResponder === self
    }
    override func mouseDown(with event: NSEvent) { if !refuse { super.mouseDown(with: event) } }
    override func keyDown(with event: NSEvent) {
        shadowRange = nil
        super.keyDown(with: event)
        if handoff, let probe = NSApp.delegate as? ProbeDelegate { window?.makeFirstResponder(probe.other) }
    }
}
@MainActor final class ProbeDelegate: NSObject, NSApplicationDelegate, NSTextViewDelegate {
    var window: NSWindow!
    var otherWindow: NSWindow!
    let target = ProbeEditor(frame: NSRect(x: 25, y: 240, width: 350, height: 40))
    let blocked = ProbeEditor(frame: NSRect(x: 25, y: 175, width: 350, height: 40))
    let handoff = ProbeEditor(frame: NSRect(x: 25, y: 110, width: 350, height: 40))
    let other = NSTextView(frame: NSRect(x: 25, y: 30, width: 350, height: 40))
    let external = NSTextView(frame: NSRect(x: 25, y: 30, width: 350, height: 40))
    let root = Bundle.main.bundleURL.deletingLastPathComponent()
    func mode() -> String {
        (try? String(contentsOf: root.appendingPathComponent("mode.txt"), encoding: .utf8)) ?? "stale"
    }
    func record() {
        let value = [
            "target": target.string, "blocked": blocked.string, "handoff": handoff.string, "other": other.string,
            "external": external.string
        ]
        try! JSONSerialization.data(withJSONObject: value, options: .sortedKeys).write(
            to: root.appendingPathComponent("values.json"), options: .atomic)
    }
    func textDidChange(_ notification: Notification) { record() }
    func applicationDidFinishLaunching(_ notification: Notification) {
        try! String(getpid()).write(to: root.appendingPathComponent("fixture.pid"), atomically: true, encoding: .utf8)
        window = NSWindow(
            contentRect: NSRect(x: 80, y: 160, width: 420, height: 320), styleMask: [.titled, .closable],
            backing: .buffered, defer: false)
        window.title = "Stale focus target"
        window.tabbingMode = .disallowed
        for (editor, id) in [(target, "target"), (blocked, "blocked"), (handoff, "handoff"), (other, "other")] {
            editor.isRichText = false
            editor.delegate = self
            editor.setAccessibilityIdentifier(id)
            window.contentView?.addSubview(editor)
        }
        blocked.refuse = true
        handoff.handoff = true
        other.string = "untouched"
        otherWindow = NSWindow(
            contentRect: NSRect(x: 80, y: 510, width: 420, height: 110), styleMask: [.titled, .closable],
            backing: .buffered, defer: false)
        otherWindow.title = "Unrelated focus document"
        otherWindow.tabbingMode = .disallowed
        external.string = "external untouched"
        external.isRichText = false
        external.delegate = self
        external.setAccessibilityIdentifier("external")
        otherWindow.contentView?.addSubview(external)
        otherWindow.orderBack(nil)
        otherWindow.makeFirstResponder(nil)
        let menu = NSMenu(), edit = NSMenu(title: "Edit")
        edit.addItem(withTitle: "Paste", action: #selector(NSText.paste(_:)), keyEquivalent: "v")
        edit.addItem(withTitle: "Select All", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")
        let item = NSMenuItem(title: "Edit", action: nil, keyEquivalent: "")
        item.submenu = edit
        menu.addItem(item)
        NSApp.mainMenu = menu
        window.orderBack(nil)
        window.makeKey()
        window.makeFirstResponder(other)
        record()
    }
}
let app = ProbeApplication.shared
app.setActivationPolicy(.regular)
let delegate = ProbeDelegate()
app.delegate = delegate
app.run()
