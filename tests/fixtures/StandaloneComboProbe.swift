import AppKit

// A writable selected value does not establish a text editor. This control
// deliberately exposes no text-selection attributes, like a select-only list.
@MainActor final class ChoiceOnlyCombo: NSView {
    var value = "12"
    override var acceptsFirstResponder: Bool { true }
    override func accessibilityRole() -> NSAccessibility.Role? { .comboBox }
    override func accessibilityValue() -> Any? { value }
    override func setAccessibilityValue(_ value: Any?) {
        self.value = String(describing: value ?? "")
        needsDisplay = true
    }
    override func isAccessibilityFocused() -> Bool { window?.firstResponder === self }
    override func setAccessibilityFocused(_ value: Bool) { if value { window?.makeFirstResponder(self) } }
    override func draw(_ rect: NSRect) {
        ("Select-only combo: \(value)" as NSString).draw(
            at: .zero, withAttributes: [.foregroundColor: NSColor.labelColor])
    }
}

@MainActor final class ComboProbe: NSObject, NSApplicationDelegate, NSComboBoxDelegate {
    let window = NSWindow(
        contentRect: NSRect(x: 280, y: 240, width: 540, height: 320), styleMask: [.titled, .closable],
        backing: .buffered, defer: false)
    let editable = NSComboBox(frame: NSRect(x: 160, y: 250, width: 250, height: 28))
    let readonly = NSComboBox(frame: NSRect(x: 160, y: 200, width: 250, height: 28))
    let disabled = NSComboBox(frame: NSRect(x: 160, y: 150, width: 250, height: 28))
    let choice = ChoiceOnlyCombo(frame: NSRect(x: 160, y: 100, width: 250, height: 28))
    let other = NSTextField(string: "untouched")
    let edit = NSMenu(title: "Edit")
    var submissions = 0
    var timer: Timer?
    let output = URL(fileURLWithPath: Bundle.main.bundlePath).deletingLastPathComponent().appendingPathComponent(
        "events.jsonl")
    func applicationDidFinishLaunching(_ notification: Notification) {
        try! String(getpid()).write(
            to: output.deletingLastPathComponent().appendingPathComponent("fixture.pid"), atomically: true,
            encoding: .utf8)
        window.title = "Standalone Combo Probe"
        let menu = NSMenu(), item = NSMenuItem()
        edit.addItem(withTitle: "Select All", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")
        edit.addItem(withTitle: "Copy", action: #selector(NSText.copy(_:)), keyEquivalent: "c")
        edit.addItem(withTitle: "Paste", action: #selector(NSText.paste(_:)), keyEquivalent: "v")
        item.submenu = edit
        menu.addItem(item)
        NSApp.mainMenu = menu
        for (field, id, label) in [
            (editable, "editable", "Editable combo"), (readonly, "readonly", "Readonly combo"),
            (disabled, "disabled", "Disabled combo")
        ] {
            field.addItems(withObjectValues: ["12", "18", "24"])
            field.stringValue = "12"
            field.setAccessibilityIdentifier(id)
            field.setAccessibilityLabel(label)
            field.delegate = self
            field.target = self
            field.action = #selector(submit)
            let text = NSTextField(labelWithString: label)
            text.frame = NSRect(x: 15, y: field.frame.minY, width: 135, height: 24)
            window.contentView?.addSubview(text)
            window.contentView?.addSubview(field)
        }
        readonly.isEditable = false
        readonly.isSelectable = true
        disabled.isEnabled = false
        choice.setAccessibilityElement(true)
        choice.setAccessibilityLabel("Select-only combo")
        window.contentView?.addSubview(choice)
        other.frame = NSRect(x: 160, y: 40, width: 250, height: 28)
        other.setAccessibilityIdentifier("other")
        other.setAccessibilityLabel("Other field")
        window.contentView?.addSubview(other)
        for (title, action, x) in [
            ("Disable Select All", #selector(disableSelectAll), 15.0),
            ("Change value on Select All", #selector(changeSelectAll), 275.0)
        ] {
            let button = NSButton(title: title, target: self, action: action)
            button.frame = NSRect(x: x, y: 5, width: 250, height: 26)
            window.contentView?.addSubview(button)
        }
        window.orderBack(nil)
        timer = Timer.scheduledTimer(withTimeInterval: 0.2, repeats: true) { [weak self] _ in
            MainActor.assumeIsolated { self?.record("sample") }
        }
        record("ready")
    }
    @objc func submit(_ sender: NSControl) {
        submissions += 1
        record("submit")
    }
    @objc func disableSelectAll() {
        if let item = edit.items.first(where: { $0.keyEquivalent == "a" }) { edit.removeItem(item) }
        record("select-all-disabled")
    }
    @objc func changeSelectAll() {
        disableSelectAll()
        let item = edit.addItem(withTitle: "Change Value", action: #selector(changeOtherValue), keyEquivalent: "a")
        item.target = self
    }
    @objc func changeOtherValue() {
        other.stringValue = "application-rewrote-value"
        record("unexpected-shortcut")
    }
    func controlTextDidChange(_ obj: Notification) {
        if obj.object as? NSComboBox === editable, editable.stringValue.contains("!") {
            window.makeFirstResponder(other)
        }
        record("changed")
    }
    func record(_ kind: String) {
        let data = try! JSONSerialization.data(withJSONObject: [
            "kind": kind, "editable": editable.stringValue, "readonly": readonly.stringValue,
            "disabled": disabled.stringValue, "choice": choice.value, "other": other.stringValue,
            "submissions": submissions
        ])
        if !FileManager.default.fileExists(atPath: output.path) {
            FileManager.default.createFile(atPath: output.path, contents: nil)
        }
        let handle = try! FileHandle(forWritingTo: output)
        try! handle.seekToEnd()
        try! handle.write(contentsOf: data + Data([10]))
        try! handle.close()
    }
}
let app = NSApplication.shared
let delegate = ComboProbe()
app.setActivationPolicy(.regular)
app.delegate = delegate
app.run()
