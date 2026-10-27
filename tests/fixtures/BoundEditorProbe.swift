import AppKit

// A legitimate AX value setter changes the view property, while the app's
// bound model follows ordinary editing notifications. AX writability alone
// does not specify those application callbacks.
@MainActor final class BoundField: NSTextField {
    override func setAccessibilityValue(_ value: Any?) { if let value = value as? String { stringValue = value } }
}

@MainActor final class ValueOnlyField: NSView {
    var value = "VALUE ONLY"
    override func accessibilityRole() -> NSAccessibility.Role? { .textField }
    override func accessibilityLabel() -> String? { "Value-only input" }
    override func accessibilityValue() -> Any? { value }
    override func setAccessibilityValue(_ value: Any?) { if let value = value as? String { self.value = value } }
    override func isAccessibilitySelectorAllowed(_ selector: Selector) -> Bool {
        if ["setAccessibilityFocused:", "setAccessibilitySelectedText:"].contains(NSStringFromSelector(selector)) {
            return false
        }
        return super.isAccessibilitySelectorAllowed(selector)
    }
}

@MainActor final class BoundEditorDelegate: NSObject, NSApplicationDelegate, NSTextFieldDelegate {
    let directory = Bundle.main.bundleURL.deletingLastPathComponent()
    let field = BoundField(string: "ORIGINAL")
    let other = NSTextField(string: "untouched")
    let valueOnly = ValueOnlyField(frame: NSRect(x: 20, y: 65, width: 400, height: 28))
    var model = "ORIGINAL"
    var changes = 0
    var window: NSWindow!
    var timer: Timer?
    func record() {
        try! JSONSerialization.data(withJSONObject: [
            "display": field.stringValue, "model": model, "changes": changes, "other": other.stringValue,
            "valueOnly": valueOnly.value
        ]).write(to: directory.appendingPathComponent("state.json"), options: .atomic)
    }
    func applicationDidFinishLaunching(_ notification: Notification) {
        window = NSWindow(
            contentRect: NSRect(x: 160, y: 180, width: 440, height: 170), styleMask: [.titled, .closable],
            backing: .buffered, defer: false)
        window.title = "Bound editor callbacks"
        window.tabbingMode = .disallowed
        let menu = NSMenu(), edit = NSMenu(title: "Edit"), item = NSMenuItem()
        edit.addItem(withTitle: "Select All", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")
        edit.addItem(withTitle: "Paste", action: #selector(NSText.paste(_:)), keyEquivalent: "v")
        item.submenu = edit
        menu.addItem(item)
        NSApp.mainMenu = menu
        field.frame = NSRect(x: 20, y: 105, width: 400, height: 28)
        field.setAccessibilityLabel("Bound input")
        field.delegate = self
        other.frame = NSRect(x: 20, y: 30, width: 400, height: 28)
        window.contentView!.addSubview(field)
        window.contentView!.addSubview(other)
        valueOnly.setAccessibilityElement(true)
        window.contentView!.addSubview(valueOnly)
        window.orderBack(nil)
        window.makeKey()
        window.makeMain()
        window.makeFirstResponder(field)
        timer = Timer.scheduledTimer(withTimeInterval: 0.03, repeats: true) { [weak self] _ in
            MainActor.assumeIsolated { self?.record() }
        }
        record()
    }
    func controlTextDidChange(_ notification: Notification) {
        model = field.stringValue
        changes += 1
        record()
    }
}

let app = NSApplication.shared
app.setActivationPolicy(.regular)
let delegate = BoundEditorDelegate()
app.delegate = delegate
app.run()
