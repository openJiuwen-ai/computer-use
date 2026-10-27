import AppKit

// Standard AppKit text editing. Logging always delegates to super; it does not
// buffer, replay, translate or commit input on behalf of the text system.
@MainActor final class LoggingTextView: NSTextView {
    var probeName = ""
    weak var probe: ProbeDelegate?
    var actualName: String { probe?.name(of: self) ?? probeName }
    override func keyDown(with event: NSEvent) {
        probe?.record("keyDown", eventDetails(event, name: actualName))
        super.keyDown(with: event)
        probe?.record("keyDown-return", ["target": actualName])
    }
    override func keyUp(with event: NSEvent) {
        probe?.record("keyUp", eventDetails(event, name: actualName))
        super.keyUp(with: event)
    }
    override func insertText(_ value: Any, replacementRange: NSRange) {
        let text = (value as? NSAttributedString)?.string ?? value as? String ?? ""
        let recipient = actualName
        probe?.record(
            "insertText",
            [
                "target": recipient, "text": text, "utf16": Array(text.utf16), "markedBefore": hasMarkedText(),
                "replacement": rangeJSON(replacementRange)
            ])
        super.insertText(value, replacementRange: replacementRange)
        probe?.record("insertText-return", ["target": recipient])
    }
    override func setMarkedText(_ value: Any, selectedRange: NSRange, replacementRange: NSRange) {
        let text = (value as? NSAttributedString)?.string ?? value as? String ?? ""
        let recipient = actualName
        probe?.record(
            "setMarkedText",
            [
                "target": recipient, "text": text, "utf16": Array(text.utf16), "selected": rangeJSON(selectedRange),
                "replacement": rangeJSON(replacementRange)
            ])
        super.setMarkedText(value, selectedRange: selectedRange, replacementRange: replacementRange)
        probe?.record("setMarkedText-return", ["target": recipient])
    }
    override func unmarkText() {
        probe?.record("unmarkText", ["target": actualName])
        super.unmarkText()
    }
    override func doCommand(by selector: Selector) {
        probe?.record("doCommand", ["target": actualName, "selector": NSStringFromSelector(selector)])
        super.doCommand(by: selector)
    }
}

func rangeJSON(_ range: NSRange) -> [String: Int] {
    ["location": range.location == NSNotFound ? -1 : range.location, "length": range.length]
}
func eventDetails(_ event: NSEvent, name: String) -> [String: Any] {
    let text = event.type == .flagsChanged ? "" : event.characters ?? ""
    return [
        "target": name, "eventType": String(describing: event.type), "characters": text, "utf16": Array(text.utf16),
        "charactersIgnoringModifiers": event.type == .flagsChanged ? "" : event.charactersIgnoringModifiers ?? "",
        "keyCode": event.keyCode, "flags": event.modifierFlags.rawValue, "eventUptime": event.timestamp,
        "repeat": event.isARepeat
    ]
}

@MainActor final class ProbeApplication: NSApplication {
    override func sendEvent(_ event: NSEvent) {
        if [.keyDown, .keyUp, .flagsChanged].contains(event.type) {
            (delegate as? ProbeDelegate)?.record("app-event", eventDetails(event, name: "application"))
        }
        super.sendEvent(event)
    }
}

@MainActor
final class ProbeDelegate: NSObject, NSApplicationDelegate, NSWindowDelegate, NSTextViewDelegate, NSTextFieldDelegate {
    let directory = Bundle.main.bundleURL.deletingLastPathComponent()
    let a = LoggingTextView(), b = LoggingTextView(), fieldEditor = LoggingTextView()
    let fieldA = NSTextField(), fieldB = NSTextField()
    var window: NSWindow!
    var sequence = 0
    func fieldName(_ field: NSTextField?) -> String {
        if field === fieldA { return "Field A" }
        if field === fieldB { return "Field B" }
        return "Field editor (owner unknown)"
    }
    func fieldOwner() -> NSTextField? {
        guard window?.firstResponder === fieldEditor else { return nil }
        // AppKit shares this editor. Asking the window for an editor does not
        // begin editing that control, so never derive ownership from that query.
        let recipient = fieldEditor.delegate as AnyObject?
        if recipient === fieldA || recipient === fieldA.cell { return fieldA }
        if recipient === fieldB || recipient === fieldB.cell { return fieldB }
        let editing = [fieldA, fieldB].filter { $0.currentEditor() === fieldEditor }
        return editing.count == 1 ? editing[0] : nil
    }
    func name(of view: LoggingTextView) -> String {
        if view === a { return "Text A" }
        if view === b { return "Text B" }
        if view === fieldEditor { return fieldName(fieldOwner()) }
        return "Unknown editor"
    }
    func currentTarget() -> String {
        if window?.firstResponder === a { return "Text A" }
        if window?.firstResponder === b { return "Text B" }
        if window?.firstResponder === fieldEditor { return name(of: fieldEditor) }
        return window?.firstResponder.map { String(describing: type(of: $0)) } ?? "none"
    }
    func state() -> [String: Any] {
        var values: [String: Any] = [:]
        for view in [a, b] {
            values[view.probeName] = [
                "text": view.string, "utf16": Array(view.string.utf16), "selected": rangeJSON(view.selectedRange()),
                "marked": rangeJSON(view.markedRange())
            ]
        }
        let owner = fieldOwner()
        for (name, field) in [("Field A", fieldA), ("Field B", fieldB)] {
            let editing = owner === field
            let visible = editing ? fieldEditor.string : field.stringValue
            values[name] = [
                "text": visible, "utf16": Array(visible.utf16), "controlValue": field.stringValue,
                "hasFieldEditor": editing
            ]
        }
        return [
            "pid": getpid(), "window": window?.windowNumber ?? 0, "sequence": sequence,
            "time": Date().timeIntervalSince1970, "uptime": ProcessInfo.processInfo.systemUptime,
            "actualFirstResponder": currentTarget(), "active": NSApp.isActive, "key": window?.isKeyWindow ?? false,
            "main": window?.isMainWindow ?? false, "fieldEditorOwner": fieldName(owner),
            "fieldEditorDelegateClass": fieldEditor.delegate.map { String(describing: type(of: $0)) } ?? "none",
            "fieldACurrentEditor": fieldA.currentEditor() === fieldEditor,
            "fieldBCurrentEditor": fieldB.currentEditor() === fieldEditor, "values": values
        ]
    }
    func writeState() {
        guard window != nil else { return }
        try! JSONSerialization.data(withJSONObject: state(), options: [.sortedKeys]).write(
            to: directory.appendingPathComponent("state.json"), options: .atomic)
    }
    func record(_ kind: String, _ details: [String: Any] = [:]) {
        guard window != nil else { return }
        sequence += 1
        var row = state()
        row["kind"] = kind
        row["details"] = details
        let data = try! JSONSerialization.data(withJSONObject: row, options: [.sortedKeys]) + Data([10])
        let path = directory.appendingPathComponent("events.jsonl")
        if let handle = try? FileHandle(forWritingTo: path) {
            _ = try? handle.seekToEnd()
            try? handle.write(contentsOf: data)
            try? handle.close()
        } else {
            try! data.write(to: path)
        }
        writeState()
    }
    func windowWillReturnFieldEditor(_ sender: NSWindow, to client: Any?) -> Any? {
        guard let field = client as? NSTextField, field === fieldA || field === fieldB else { return nil }
        return fieldEditor
    }
    func textDidChange(_ notification: Notification) { record("textDidChange") }
    func controlTextDidChange(_ notification: Notification) {
        record("controlTextDidChange", ["control": fieldName(notification.object as? NSTextField)])
    }
    func controlTextDidBeginEditing(_ notification: Notification) {
        record("controlTextDidBeginEditing", ["control": fieldName(notification.object as? NSTextField)])
    }
    func controlTextDidEndEditing(_ notification: Notification) {
        record("controlTextDidEndEditing", ["control": fieldName(notification.object as? NSTextField)])
    }
    func applicationDidFinishLaunching(_ notification: Notification) {
        try! String(getpid()).write(
            to: directory.appendingPathComponent("fixture.pid"), atomically: true, encoding: .utf8)
        window = NSWindow(
            contentRect: NSRect(x: 140, y: 110, width: 600, height: 430), styleMask: [.titled, .closable],
            backing: .buffered, defer: false)
        window.title = "Unicode Input Probe"
        window.tabbingMode = .disallowed
        window.isReleasedWhenClosed = false
        window.delegate = self
        for (view, name, y) in [(a, "Text A", 275.0), (b, "Text B", 135.0)] {
            view.probeName = name
            view.probe = self
            view.delegate = self
            view.frame = NSRect(x: 20, y: y, width: 560, height: 110)
            view.isRichText = false
            view.font = .systemFont(ofSize: 17)
            view.setAccessibilityLabel(name)
            view.setAccessibilityIdentifier(name.replacingOccurrences(of: " ", with: "-"))
            window.contentView?.addSubview(view)
            let label = NSTextField(labelWithString: name)
            label.frame = NSRect(x: 20, y: y + 111, width: 200, height: 21)
            window.contentView?.addSubview(label)
        }
        fieldEditor.isFieldEditor = true
        fieldEditor.isRichText = false
        fieldEditor.probe = self
        for (field, name, x) in [(fieldA, "Field A", 20.0), (fieldB, "Field B", 315.0)] {
            field.frame = NSRect(x: x, y: 40, width: 265, height: 28)
            field.delegate = self
            field.font = .systemFont(ofSize: 17)
            field.setAccessibilityLabel(name)
            field.setAccessibilityIdentifier(name.replacingOccurrences(of: " ", with: "-"))
            window.contentView?.addSubview(field)
            let label = NSTextField(labelWithString: name)
            label.frame = NSRect(x: x, y: 73, width: 200, height: 21)
            window.contentView?.addSubview(label)
        }
        let main = NSMenu(), app = NSMenuItem(), commands = NSMenu()
        commands.addItem(withTitle: "Quit Probe", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        app.submenu = commands
        main.addItem(app)
        let edit = NSMenuItem(title: "Edit", action: nil, keyEquivalent: ""), menu = NSMenu(title: "Edit")
        for (title, action, key) in [
            ("Select All", #selector(NSText.selectAll(_:)), "a"), ("Copy", #selector(NSText.copy(_:)), "c"),
            ("Paste", #selector(NSText.paste(_:)), "v")
        ] { menu.addItem(withTitle: title, action: action, keyEquivalent: key) }
        edit.submenu = menu
        main.addItem(edit)
        NSApp.mainMenu = main
        window.orderBack(nil)
        window.makeKey()
        window.makeMain()
        window.makeFirstResponder(a)
        record("ready")
        Timer.scheduledTimer(withTimeInterval: 0.1, repeats: true) { [weak self] _ in
            MainActor.assumeIsolated { self?.writeState() }
        }
        Timer.scheduledTimer(withTimeInterval: 180, repeats: false) { _ in
            MainActor.assumeIsolated { NSApp.terminate(nil) }
        }
    }
}
let app = ProbeApplication.shared
app.setActivationPolicy(.regular)
let delegate = ProbeDelegate()
app.delegate = delegate
app.run()
