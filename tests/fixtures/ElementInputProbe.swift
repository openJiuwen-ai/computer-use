import AppKit

@MainActor final class InputProbeApplication: NSApplication {
    override func sendEvent(_ event: NSEvent) {
        if event.type == .mouseMoved, let probe = delegate as? FocusProbeDelegate, probe.resizeControlOnNextMouseEvent {
            probe.resizeControlOnNextMouseEvent = false
            probe.mouse.frame.size.width -= 20
            probe.record("mouse-control-resized", extra: ["presses": probe.mouse.presses])
        }
        if event.type == .mouseMoved, let probe = delegate as? FocusProbeDelegate, probe.moveControlOnNextMouseEvent {
            probe.moveControlOnNextMouseEvent = false
            probe.mouse.frame.origin.x += 200
            probe.record("mouse-control-moved", extra: ["presses": probe.mouse.presses])
        }
        if [.leftMouseDown, .leftMouseUp, .rightMouseDown, .rightMouseUp, .otherMouseDown, .otherMouseUp].contains(
            event.type), let probe = delegate as? FocusProbeDelegate, probe.log != nil
        {
            let content = event.window?.contentView
            let hit = content?.hitTest(event.locationInWindow)
            probe.record(
                "mouse",
                extra: [
                    "type": event.type.rawValue,
                    "finitePoint": event.locationInWindow.x.isFinite && event.locationInWindow.y.isFinite,
                    "x": event.locationInWindow.x, "y": event.locationInWindow.y,
                    "inContent": content.map { $0.bounds.contains($0.convert(event.locationInWindow, from: nil)) }
                        ?? false, "windowKey": event.window?.isKeyWindow ?? false,
                    "hit": hit.map { String(describing: type(of: $0)) } ?? "none",
                    "acceptsFirstMouse": hit?.acceptsFirstMouse(for: event) ?? false,
                    "flags": event.modifierFlags.rawValue
                ])
        }
        super.sendEvent(event)
    }
}

@MainActor final class MouseFocusField: NSTextField {
    override func setAccessibilityFocused(_ focused: Bool) { /* Simulate an advertised but ineffective AX setter. */  }
}

// Reuse the same native control while changing its backing context during
// focus preparation. No application-specific workflow is involved.
@MainActor final class ContextSwitchField: NSTextField {
    var onFocus: (() -> Void)?
    override func setAccessibilityFocused(_ focused: Bool) {
        if focused, let action = onFocus {
            onFocus = nil
            action()
        }
        super.setAccessibilityFocused(focused)
    }
}

@MainActor class NumericField: NSView {
    let editor: NSTextField
    init(string: String) {
        editor = NSTextField(string: string)
        super.init(frame: .zero)
        setAccessibilityElement(true)
        editor.autoresizingMask = [.width, .height]
        editor.setAccessibilityIdentifier("numeric-editor")
        addSubview(editor)
    }
    required init?(coder: NSCoder) { fatalError("init(coder:) has not been implemented") }
    override func layout() {
        super.layout()
        editor.frame = bounds
    }
    override func accessibilityRole() -> NSAccessibility.Role? { .unknown }
    override func accessibilityValue() -> Any? { Int(editor.stringValue) ?? 0 }
    override func setAccessibilityValue(_ value: Any?) {
        if let value = value as? NSNumber { editor.stringValue = value.stringValue }
    }
}

// A table cell may itself have a text-field role while its child is the
// editor. Its numeric AXValue is not the text inside that child.
@MainActor final class EditorCell: NumericField {
    override func accessibilityRole() -> NSAccessibility.Role? { .textField }
    override func accessibilityValue() -> Any? { 0 }
}

@MainActor final class EditorTable: NSView {
    var selected = [Any]()
    override func accessibilityRole() -> NSAccessibility.Role? { .table }
    override func accessibilitySelectedChildren() -> [Any]? { selected }
    override func setAccessibilitySelectedChildren(_ children: [Any]?) { selected = children ?? [] }
}

@MainActor final class KeyboardCheckbox: NSView {
    var checked = false
    var activations = 0
    override var acceptsFirstResponder: Bool { true }
    override func isAccessibilityFocused() -> Bool { window?.firstResponder === self }
    override func setAccessibilityFocused(_ focused: Bool) { if focused { window?.makeFirstResponder(self) } }
    override func accessibilityValue() -> Any? { checked ? 1 : 0 }
    override func keyDown(with event: NSEvent) {
        if event.characters == " " {
            checked.toggle()
            activations += 1
            needsDisplay = true
        } else {
            super.keyDown(with: event)
        }
    }
    override func draw(_ rect: NSRect) {
        ("Keyboard checkbox: \(checked), activations: \(activations)" as NSString).draw(
            at: .zero, withAttributes: [.foregroundColor: NSColor.labelColor])
    }
}

@MainActor final class MouseOnlyButton: NSView {
    var presses = 0
    override var isFlipped: Bool { true }
    override func mouseDown(with event: NSEvent) {
        presses += 1
        setAccessibilityValue(String(presses))
        needsDisplay = true
    }
    override func draw(_ dirtyRect: NSRect) {
        NSColor.controlBackgroundColor.setFill()
        bounds.fill()
        ("Mouse button: \(presses)" as NSString).draw(
            at: NSPoint(x: 8, y: 8), withAttributes: [.foregroundColor: NSColor.labelColor])
    }
}

@MainActor final class FailingPressButton: NSButton {
    var presses = 0
    override func accessibilityPerformPress() -> Bool {
        presses += 1
        setAccessibilityValue(String(presses))
        return false
    }
}

// A dropdown may expose AXShowMenu without AXPress. Record the native callback
// visibly, without needing a business application's menus or a mouse fallback.
@MainActor final class ShowMenuControl: NSView {
    var opens = 0
    override func accessibilityPerformShowMenu() -> Bool {
        opens += 1
        setAccessibilityValue(String(opens))
        needsDisplay = true
        return true
    }
    override func draw(_ rect: NSRect) {
        ("Menus opened: \(opens)" as NSString).draw(at: .zero, withAttributes: [.foregroundColor: NSColor.labelColor])
    }
}

@MainActor final class FocusProbeDelegate: NSObject, NSApplicationDelegate, NSTextFieldDelegate {
    var window: NSWindow!
    let first = ContextSwitchField(string: "A:")
    let second = NSTextField(string: "B:")
    let locked = NSTextField(string: "LOCKED")
    let fallback = MouseFocusField(string: "C:")
    let mouse = MouseOnlyButton()
    let failing = FailingPressButton(title: "Failing native press", target: nil, action: nil)
    let nested = NSTextField(string: "D:")
    let numeric = NumericField(string: "2")
    let checkbox = KeyboardCheckbox()
    let cell = EditorCell(string: "cell contents")
    var moveControlOnNextMouseEvent = false
    var resizeControlOnNextMouseEvent = false
    var log: FileHandle!

    func focused() -> String {
        if let editor = window?.firstResponder as? NSTextView, let field = editor.delegate as? NSTextField {
            return field.identifier?.rawValue ?? "unknown-field"
        }
        return "none"
    }

    func record(_ kind: String, extra: [String: Any] = [:]) {
        var value: [String: Any] = [
            "kind": kind, "focus": focused(), "first": first.stringValue, "second": second.stringValue,
            "active": NSApplication.shared.isActive,
            "foregroundPID": NSWorkspace.shared.frontmostApplication?.processIdentifier ?? 0,
            "time": Date().timeIntervalSince1970
        ]
        for (key, item) in extra { value[key] = item }
        try? log.write(contentsOf: JSONSerialization.data(withJSONObject: value) + Data([10]))
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        let url = Bundle.main.bundleURL.deletingLastPathComponent().appendingPathComponent("events.jsonl")
        FileManager.default.createFile(atPath: url.path, contents: nil)
        log = try! FileHandle(forWritingTo: url)
        window = NSWindow(
            contentRect: NSRect(x: 0, y: 0, width: 520, height: 680), styleMask: [.titled, .closable],
            backing: .buffered, defer: false)
        window.title = "Computer Use Input Acceptance"
        window.representedURL = url.deletingLastPathComponent().appendingPathComponent("document-initial.txt")
        window.isReleasedWhenClosed = false
        window.autorecalculatesKeyViewLoop = false
        for (field, id, label, y) in [(first, "field-a", "Field A", 130.0), (second, "field-b", "Field B", 70.0)] {
            field.frame = NSRect(x: 100, y: y, width: 390, height: 28)
            field.identifier = NSUserInterfaceItemIdentifier(id)
            field.setAccessibilityIdentifier(id)
            field.setAccessibilityLabel(label)
            field.delegate = self
            let title = NSTextField(labelWithString: label)
            title.frame = NSRect(x: 20, y: y, width: 75, height: 28)
            window.contentView?.addSubview(title)
            window.contentView?.addSubview(field)
        }
        locked.frame = NSRect(x: 100, y: 20, width: 390, height: 28)
        locked.isEditable = false
        locked.setAccessibilityIdentifier("read-only")
        window.contentView?.addSubview(locked)
        fallback.frame = NSRect(x: 100, y: 200, width: 390, height: 28)
        fallback.setAccessibilityIdentifier("fallback-field")
        window.contentView?.addSubview(fallback)
        mouse.frame = NSRect(x: 100, y: 245, width: 185, height: 32)
        mouse.setAccessibilityElement(true)
        mouse.setAccessibilityRole(.button)
        mouse.setAccessibilityLabel("Mouse only button")
        mouse.setAccessibilityIdentifier("mouse-button")
        mouse.setAccessibilityValue("0")
        window.contentView?.addSubview(mouse)
        failing.frame = NSRect(x: 295, y: 245, width: 190, height: 32)
        failing.setAccessibilityIdentifier("failing-button")
        failing.setAccessibilityValue("0")
        window.contentView?.addSubview(failing)
        let outer = NSView(frame: NSRect(x: 100, y: 300, width: 390, height: 100))
        outer.setAccessibilityElement(true)
        outer.setAccessibilityRole(.group)
        outer.setAccessibilityIdentifier("nested-input")
        outer.setAccessibilityLabel("Nested input")
        let inner = NSView(frame: NSRect(x: 0, y: 0, width: 390, height: 90))
        inner.setAccessibilityElement(true)
        inner.setAccessibilityRole(.group)
        nested.frame = NSRect(x: 10, y: 25, width: 350, height: 28)
        nested.setAccessibilityIdentifier("nested-field")
        inner.addSubview(nested)
        outer.addSubview(inner)
        window.contentView?.addSubview(outer)
        numeric.frame = NSRect(x: 100, y: 425, width: 200, height: 28)
        numeric.setAccessibilityIdentifier("numeric-input")
        window.contentView?.addSubview(numeric)
        let moveButton = NSButton(
            title: "Move control on pointer move", target: self, action: #selector(armPointerMove))
        moveButton.frame = NSRect(x: 310, y: 425, width: 200, height: 28)
        moveButton.setAccessibilityIdentifier("arm-pointer-move")
        window.contentView?.addSubview(moveButton)
        let resizeButton = NSButton(
            title: "Resize control on pointer move", target: self, action: #selector(armPointerResize))
        resizeButton.frame = NSRect(x: 310, y: 458, width: 200, height: 28)
        resizeButton.setAccessibilityIdentifier("arm-pointer-resize")
        window.contentView?.addSubview(resizeButton)
        checkbox.frame = NSRect(x: 100, y: 490, width: 390, height: 28)
        checkbox.setAccessibilityElement(true)
        checkbox.setAccessibilityRole(.checkBox)
        checkbox.setAccessibilityIdentifier("keyboard-checkbox")
        window.contentView?.addSubview(checkbox)
        for (id, title, action, x) in [
            ("arm-document-focus", "Switch document on focus", #selector(armDocumentFocus), 100.0),
            ("arm-record-focus", "Switch record on focus", #selector(armRecordFocus), 300.0)
        ] {
            let button = NSButton(title: title, target: self, action: action)
            button.frame = NSRect(x: x, y: 530, width: 190, height: 28)
            button.setAccessibilityIdentifier(id)
            window.contentView?.addSubview(button)
        }
        let table = EditorTable(frame: NSRect(x: 100, y: 565, width: 390, height: 40))
        table.setAccessibilityElement(true)
        cell.frame = table.bounds
        cell.setAccessibilityIdentifier("editor-cell")
        cell.editor.setAccessibilityIdentifier("cell-editor")
        table.addSubview(cell)
        window.contentView?.addSubview(table)
        for (id, x, enabled) in [("show-menu", 100.0, true), ("show-menu-disabled", 300.0, false)] {
            let control = ShowMenuControl(frame: NSRect(x: x, y: 625, width: 185, height: 28))
            control.setAccessibilityElement(true)
            control.setAccessibilityRole(.popUpButton)
            control.setAccessibilityIdentifier(id)
            control.setAccessibilityValue("0")
            control.setAccessibilityEnabled(enabled)
            window.contentView?.addSubview(control)
        }
        let menu = NSMenu(), item = NSMenuItem(), commands = NSMenu()
        commands.addItem(
            withTitle: "Quit Input Probe", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        item.submenu = commands
        menu.addItem(item)
        NSApplication.shared.mainMenu = menu
        first.nextKeyView = second
        second.nextKeyView = first
        window.initialFirstResponder = first
        NSEvent.addLocalMonitorForEvents(matching: [.keyDown, .keyUp]) { [weak self] event in
            self?.record(
                "key",
                extra: [
                    "type": event.type.rawValue, "code": event.keyCode, "chars": event.characters ?? "",
                    "flags": event.modifierFlags.rawValue
                ])
            return event
        }
        window.center()
        window.orderBack(nil)
        window.makeKey()
        window.makeMain()
        window.makeFirstResponder(first)
        first.currentEditor()?.selectedRange = NSRange(location: first.stringValue.utf16.count, length: 0)
        record("ready")
    }

    func controlTextDidChange(_ notification: Notification) {
        if let field = notification.object as? NSTextField, field === second {
            if second.stringValue.hasPrefix("#") {
                window.makeFirstResponder(first)
            } else if second.stringValue.hasPrefix("%") || second.stringValue.hasPrefix("$") {
                switchDocument(second.stringValue.hasPrefix("%") ? "element-key" : "app-key")
            } else if second.stringValue.hasPrefix("~") {
                second.stringValue = second.stringValue.uppercased()
            } else if second.stringValue.hasPrefix("@"), let editor = second.currentEditor() {
                // A normal completion edits Value and selects the suggested suffix.
                // The next typed character replaces that suffix, keeping the prefix.
                let entered = second.stringValue
                second.stringValue = entered + "-suggestion"
                editor.selectedRange = NSRange(location: entered.utf16.count, length: "-suggestion".utf16.count)
            }
        }
        record("changed")
    }
    private func switchDocument(_ context: String) {
        window.representedURL = Bundle.main.bundleURL.deletingLastPathComponent().appendingPathComponent(
            "document-\(context).txt")
        record("document-\(context)")
    }
    @objc func armDocumentFocus() {
        first.onFocus = { [weak self] in self?.switchDocument("focus") }
        record("arm-document-focus")
    }
    @objc func armRecordFocus() {
        first.onFocus = { [weak self] in
            self?.first.setAccessibilityIdentifier("record-new")
            self?.record("record-changed")
        }
        record("arm-record-focus")
    }
    @objc func armPointerMove() {
        moveControlOnNextMouseEvent = true
        record("arm-pointer-move")
    }
    @objc func armPointerResize() {
        resizeControlOnNextMouseEvent = true
        record("arm-pointer-resize")
    }
    func controlTextDidBeginEditing(_ notification: Notification) { record("beginEditing") }
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }
}

let application = InputProbeApplication.shared
application.setActivationPolicy(.regular)
let delegate = FocusProbeDelegate()
application.delegate = delegate
application.run()
