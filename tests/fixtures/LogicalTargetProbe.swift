import AppKit

private let rowKey = NSAccessibility.Attribute(rawValue: "AXRowIndexRange")
private let columnKey = NSAccessibility.Attribute(rawValue: "AXColumnIndexRange")

/// The same native view represents a different virtual item after recycling.
/// Its label, identifier, window, parent and editable value need not change.
@MainActor final class LogicalField: NSTextField {
    var row = 0
    var column = 0
    override func accessibilityAttributeNames() -> [NSAccessibility.Attribute] {
        Array(Set(super.accessibilityAttributeNames() + [rowKey, columnKey]))
    }
    override func accessibilityAttributeValue(_ attribute: NSAccessibility.Attribute) -> Any? {
        if attribute == rowKey { return NSValue(range: NSRange(location: row, length: 1)) }
        if attribute == columnKey { return NSValue(range: NSRange(location: column, length: 1)) }
        return super.accessibilityAttributeValue(attribute)
    }
    override func accessibilityRowIndexRange() -> NSRange { NSRange(location: row, length: 1) }
    override func accessibilityColumnIndexRange() -> NSRange { NSRange(location: column, length: 1) }
}

@MainActor final class LogicalOwner: NSView {
    var row = 0
    override func accessibilityAttributeNames() -> [NSAccessibility.Attribute] {
        Array(Set(super.accessibilityAttributeNames() + [rowKey, columnKey]))
    }
    override func accessibilityAttributeValue(_ attribute: NSAccessibility.Attribute) -> Any? {
        if attribute == rowKey { return NSValue(range: NSRange(location: row, length: 1)) }
        if attribute == columnKey { return NSValue(range: NSRange(location: 2, length: 1)) }
        return super.accessibilityAttributeValue(attribute)
    }
    override func accessibilityRowIndexRange() -> NSRange { NSRange(location: row, length: 1) }
    override func accessibilityColumnIndexRange() -> NSRange { NSRange(location: 2, length: 1) }
    override func accessibilityChildren() -> [Any]? { subviews }
    override func isAccessibilitySelectorAllowed(_ selector: Selector) -> Bool {
        if selector == #selector(setAccessibilityFocused(_:)) || selector == #selector(setAccessibilityValue(_:)) {
            return false
        }
        return super.isAccessibilitySelectorAllowed(selector)
    }
}

@MainActor final class ContextField: NSTextField {
    var didFocus: (() -> Void)?
    var didSelect: ((NSRange) -> Void)?
    override func becomeFirstResponder() -> Bool {
        let result = super.becomeFirstResponder()
        if result { didFocus?() }
        return result
    }
    override func accessibilitySelectedTextRange() -> NSRange {
        currentEditor()?.selectedRange ?? super.accessibilitySelectedTextRange()
    }
    override func setAccessibilitySelectedTextRange(_ range: NSRange) {
        if let editor = currentEditor() {
            editor.selectedRange = range
        } else {
            super.setAccessibilitySelectedTextRange(range)
        }
        didSelect?(range)
    }
}

@MainActor final class Delegate: NSObject, NSApplicationDelegate {
    let output = URL(fileURLWithPath: CommandLine.arguments[1])
    let direct = LogicalField(frame: NSRect(x: 25, y: 225, width: 370, height: 26))
    let parent = LogicalOwner(frame: NSRect(x: 20, y: 135, width: 380, height: 60))
    let child = NSTextField(frame: NSRect(x: 5, y: 15, width: 370, height: 26))
    let owner = LogicalOwner(frame: NSRect(x: 20, y: 60, width: 380, height: 60))
    let editor = ContextField(frame: NSRect(x: 5, y: 15, width: 370, height: 26))
    let other = NSTextField(frame: NSRect(x: 25, y: 20, width: 370, height: 26))
    var window: NSWindow!
    var timer: Timer?
    var handled = ""
    var focusRace = false
    var selectionRace = false
    var focusChanges = 0
    var selectionChanges = 0

    func applicationDidFinishLaunching(_ notification: Notification) {
        window = NSWindow(
            contentRect: NSRect(x: 160, y: 220, width: 420, height: 285), styleMask: [.titled, .closable],
            backing: .buffered, defer: false)
        window.title = "Logical item identity fixture"
        direct.row = 1
        direct.column = 7
        direct.setAccessibilityIdentifier("logical-direct")
        direct.stringValue = "direct original"
        parent.row = 2
        parent.setAccessibilityElement(true)
        parent.setAccessibilityRole(.cell)
        // Deliberately no identifier/label on this ancestor. Its table position
        // is the only item boundary between the window and the editor.
        child.setAccessibilityIdentifier("logical-child")
        child.stringValue = "child original"
        parent.addSubview(child)
        owner.row = 3
        owner.setAccessibilityElement(true)
        owner.setAccessibilityRole(.textField)
        owner.setAccessibilityIdentifier("logical-owner")
        editor.setAccessibilityIdentifier("logical-editor")
        editor.stringValue = "editor original"
        owner.addSubview(editor)
        other.setAccessibilityIdentifier("logical-other")
        other.stringValue = "untouched"
        for view in [direct, parent, owner, other] { window.contentView!.addSubview(view) }
        editor.didFocus = { [weak self] in
            guard let self, self.focusRace else { return }
            self.owner.row += 1
            self.focusChanges += 1
            self.focusRace = false
        }
        editor.didSelect = { [weak self] range in
            guard let self, self.selectionRace, range.length > 0 else { return }
            self.owner.row += 1
            self.selectionChanges += 1
            self.selectionRace = false
        }
        window.orderBack(nil)
        window.makeFirstResponder(other)
        timer = Timer.scheduledTimer(withTimeInterval: 0.03, repeats: true) { [weak self] _ in
            Task { @MainActor in self?.tick() }
        }
        tick()
    }

    func tick() {
        let request = output.appendingPathComponent("command")
        if let command = try? String(contentsOf: request, encoding: .utf8) {
            try? FileManager.default.removeItem(at: request)
            switch command {
            case "format-direct": direct.stringValue = "formatted value"
            case "reuse-direct": direct.row += 10
            case "reuse-parent": parent.row += 10
            case "arm-focus":
                window.makeFirstResponder(other)
                focusRace = true
            case "arm-selection":
                editor.currentEditor()?.selectedRange = NSRange(location: 0, length: 0)
                selectionRace = true
            default: break
            }
            handled = command
        }
        let state: [String: Any] = [
            "direct": direct.currentEditor()?.string ?? direct.stringValue,
            "child": child.currentEditor()?.string ?? child.stringValue,
            "editor": editor.currentEditor()?.string ?? editor.stringValue,
            "other": other.currentEditor()?.string ?? other.stringValue, "directRow": direct.row,
            "parentRow": parent.row, "ownerRow": owner.row, "focusChanges": focusChanges,
            "selectionChanges": selectionChanges, "handled": handled
        ]
        try! JSONSerialization.data(withJSONObject: state, options: [.sortedKeys]).write(
            to: output.appendingPathComponent("state.json"), options: .atomic)
    }
}

let app = NSApplication.shared
let delegate = Delegate()
app.delegate = delegate
app.setActivationPolicy(.regular)
app.run()
