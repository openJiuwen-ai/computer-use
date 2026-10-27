import AppKit

@MainActor final class PropertyApplication: NSApplication {
    override func accessibilityApplicationFocusedUIElement() -> Any? {
        (delegate as? PropertyDelegate)?.window?.contentView
    }
}

@MainActor final class PropertyContainer: NSView {
    override func isAccessibilityElement() -> Bool { true }
    override func accessibilityRole() -> NSAccessibility.Role? { .group }
    override func isAccessibilityFocused() -> Bool { false }
}

// A scalar property can be writable even when its declared focus setter fails
// to establish any observable keyboard receiver. Neither is an editing oracle.
@MainActor final class PropertyField: NSView {
    var value = "ORIGINAL"
    var writes = 0
    var inert = false
    var readOnly = false
    override func isAccessibilityElement() -> Bool { true }
    override func accessibilityRole() -> NSAccessibility.Role? { .textField }
    override func accessibilityValue() -> Any? { value }
    override func setAccessibilityValue(_ value: Any?) {
        writes += 1
        if !inert, let value = value as? String {
            self.value = value
            needsDisplay = true
        }
    }
    override func isAccessibilityFocused() -> Bool { false }
    override func setAccessibilityFocused(_ focused: Bool) {}
    override func accessibilitySelectedTextRange() -> NSRange { NSRange(location: 0, length: 0) }
    override func setAccessibilitySelectedTextRange(_ range: NSRange) {}
    override func isAccessibilitySelectorAllowed(_ selector: Selector) -> Bool {
        if selector == #selector(setAccessibilitySelectedText(_:)) { return false }
        if selector == #selector(setAccessibilityValue(_:)) { return !readOnly }
        return super.isAccessibilitySelectorAllowed(selector)
    }
    override func draw(_ dirtyRect: NSRect) {
        NSColor.controlBackgroundColor.setFill()
        bounds.fill()
        ((accessibilityLabel()! + ": " + value) as NSString).draw(
            at: NSPoint(x: 6, y: 8), withAttributes: [.foregroundColor: NSColor.labelColor])
    }
}

// Property writes intentionally omit the bound model's normal edit callback.
// A successful regression must preserve ordinary keyboard editing here.
@MainActor final class BoundPropertyField: NSTextField {
    var writes = 0
    override func setAccessibilityValue(_ value: Any?) {
        writes += 1
        if let value = value as? String { stringValue = value }
    }
}

@MainActor final class PropertyDelegate: NSObject, NSApplicationDelegate, NSTextFieldDelegate {
    let directory = Bundle.main.bundleURL.deletingLastPathComponent()
    let target = PropertyField(), inert = PropertyField(), readOnly = PropertyField()
    let normal = BoundPropertyField(string: "NORMAL"), guardField = NSTextField(string: "GUARD")
    var window: NSWindow!
    var model = "NORMAL", changes = 0
    var timer: Timer?
    func record() {
        let state: [String: Any] = [
            "target": target.value, "writes": target.writes, "inert": inert.value, "inertWrites": inert.writes,
            "readOnly": readOnly.value, "readOnlyWrites": readOnly.writes, "normal": normal.stringValue,
            "normalWrites": normal.writes, "model": model, "changes": changes, "guard": guardField.stringValue
        ]
        try! JSONSerialization.data(withJSONObject: state).write(
            to: directory.appendingPathComponent("state.json"), options: .atomic)
    }
    func controlTextDidChange(_ notification: Notification) {
        model = normal.stringValue
        changes += 1
        record()
    }
    func applicationDidFinishLaunching(_ notification: Notification) {
        window = NSWindow(
            contentRect: NSRect(x: 160, y: 180, width: 480, height: 260), styleMask: [.titled, .closable],
            backing: .buffered, defer: false)
        window.title = "Property replacement"
        window.tabbingMode = .disallowed
        window.contentView = PropertyContainer(frame: NSRect(x: 0, y: 0, width: 480, height: 260))
        inert.inert = true
        readOnly.readOnly = true
        for (index, entry) in [(target, "Property input"), (inert, "Inert property"), (readOnly, "Read-only property")]
            .enumerated()
        {
            entry.0.frame = NSRect(x: 20, y: 210 - index * 42, width: 440, height: 30)
            entry.0.setAccessibilityLabel(entry.1)
            window.contentView!.addSubview(entry.0)
        }
        normal.frame = NSRect(x: 20, y: 75, width: 440, height: 28)
        normal.setAccessibilityLabel("Bound input")
        normal.delegate = self
        guardField.frame = NSRect(x: 20, y: 25, width: 440, height: 28)
        guardField.setAccessibilityLabel("Guard input")
        window.contentView!.addSubview(normal)
        window.contentView!.addSubview(guardField)
        let menu = NSMenu(), edit = NSMenuItem(title: "Edit", action: nil, keyEquivalent: "")
        edit.submenu = NSMenu(title: "Edit")
        edit.submenu!.addItem(withTitle: "Select All", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")
        menu.addItem(edit)
        NSApp.mainMenu = menu
        window.orderBack(nil)
        window.makeKey()
        window.makeMain()
        window.makeFirstResponder(guardField)
        timer = Timer.scheduledTimer(withTimeInterval: 0.03, repeats: true) { _ in
            MainActor.assumeIsolated { self.record() }
        }
        record()
    }
}
let app = PropertyApplication.shared
app.setActivationPolicy(.regular)
let delegate = PropertyDelegate()
app.delegate = delegate
app.run()
