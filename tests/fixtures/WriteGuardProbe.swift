import AppKit

@MainActor final class GuardObjectField: NSView {
    var value = "OBJECT ORIGINAL"
    var writes = 0
    override func isAccessibilityElement() -> Bool { true }
    override func accessibilityRole() -> NSAccessibility.Role? { .textField }
    override func accessibilityLabel() -> String? { "Object input" }
    override func accessibilityValue() -> Any? { value }
    override func setAccessibilityValue(_ value: Any?) {
        if let value = value as? String {
            self.value = value
            writes += 1
            needsDisplay = true
        }
    }
    override func isAccessibilitySelectorAllowed(_ selector: Selector) -> Bool {
        if ["setAccessibilityFocused:", "setAccessibilitySelectedText:"].contains(NSStringFromSelector(selector)) {
            return false
        }
        return super.isAccessibilitySelectorAllowed(selector)
    }
    override func draw(_ dirtyRect: NSRect) {
        NSColor.textBackgroundColor.setFill()
        bounds.fill()
        (value as NSString).draw(
            at: NSPoint(x: 5, y: 6),
            withAttributes: [.font: NSFont.systemFont(ofSize: 14), .foregroundColor: NSColor.textColor])
    }
}

@MainActor final class WriteGuardProbe: NSObject, NSApplicationDelegate, NSTextFieldDelegate {
    let directory = Bundle.main.bundleURL.deletingLastPathComponent()
    let object = GuardObjectField(frame: NSRect(x: 20, y: 120, width: 420, height: 30))
    let editor = NSTextField(string: "EDITOR ORIGINAL")
    let adjacent = NSTextField(string: "GUARD")
    var changes = 0, submits = 0
    var window: NSWindow!
    func record() {
        try! JSONSerialization.data(withJSONObject: [
            "object": object.value, "writes": object.writes, "editor": editor.stringValue,
            "guard": adjacent.stringValue, "changes": changes, "submits": submits, "pid": getpid()
        ]).write(to: directory.appendingPathComponent("state.json"), options: .atomic)
    }
    func applicationDidFinishLaunching(_ notification: Notification) {
        window = NSWindow(
            contentRect: NSRect(x: 140, y: 180, width: 460, height: 180), styleMask: [.titled, .closable],
            backing: .buffered, defer: false)
        window.title = "Write guard acceptance"
        window.tabbingMode = .disallowed
        editor.frame = NSRect(x: 20, y: 75, width: 420, height: 28)
        editor.setAccessibilityLabel("Context input")
        editor.delegate = self
        adjacent.frame = NSRect(x: 20, y: 30, width: 420, height: 28)
        adjacent.setAccessibilityLabel("Adjacent guard")
        window.contentView!.addSubview(object)
        window.contentView!.addSubview(editor)
        window.contentView!.addSubview(adjacent)
        window.orderBack(nil)
        window.makeKey()
        window.makeMain()
        window.makeFirstResponder(editor)
        Timer.scheduledTimer(withTimeInterval: 0.03, repeats: true) { _ in MainActor.assumeIsolated { self.record() } }
        record()
    }
    func controlTextDidChange(_ notification: Notification) {
        changes += 1
        record()
    }
    func control(_ control: NSControl, textView: NSTextView, doCommandBy commandSelector: Selector) -> Bool {
        if control === editor && commandSelector == #selector(NSResponder.insertNewline(_:)) {
            submits += 1
            record()
            return true
        }
        return false
    }
}

let app = NSApplication.shared
app.setActivationPolicy(.regular)
let delegate = WriteGuardProbe()
app.delegate = delegate
app.run()
