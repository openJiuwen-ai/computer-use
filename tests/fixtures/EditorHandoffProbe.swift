import AppKit

@MainActor final class HandoffField: NSTextField {
    var omitParent = false
    weak var conflictingParent: NSView?
    override func accessibilityParent() -> Any? {
        if let conflictingParent { return conflictingParent }
        return omitParent ? nil : super.accessibilityParent()
    }
}

@MainActor final class EditingRegion: NSView {
    var selectedEditor: NSView?
    override func accessibilityChildren() -> [Any]? { subviews }
    override func accessibilitySelectedChildren() -> [Any]? { selectedEditor.map { [$0] } ?? [] }
}

@MainActor final class EditingCollection: NSView {
    weak var host: EditorHost?
    override func accessibilityChildren() -> [Any]? { subviews }
    override func isAccessibilityFocused() -> Bool {
        guard let host, let responder = window?.firstResponder else { return false }
        return responder === host
    }
}

// A control enters edit mode only after its first printable key. Its editor
// is a real NSTextField under two transparent containers, created at runtime.
@MainActor final class EditorHost: NSView {
    let mode: String
    var editor: HandoffField?
    var onChange: (() -> Void)?
    weak var other: NSTextField?
    init(mode: String, frame: NSRect) {
        self.mode = mode
        super.init(frame: frame)
        setAccessibilityElement(true)
        setAccessibilityIdentifier(mode)
        setAccessibilityLabel("Delayed editor " + mode)
    }
    required init?(coder: NSCoder) { fatalError("init(coder:) not implemented") }
    override var acceptsFirstResponder: Bool { true }
    override func accessibilityRole() -> NSAccessibility.Role? { .textField }
    // The host keeps its own metadata value. The live editor owns typed text.
    override func accessibilityValue() -> Any? { "" }
    override func setAccessibilityValue(_ value: Any?) {}
    override func accessibilitySelectedTextRange() -> NSRange { NSRange(location: 0, length: 0) }
    override func setAccessibilitySelectedTextRange(_ range: NSRange) {}
    override func isAccessibilityFocused() -> Bool { window?.firstResponder === self }
    override func setAccessibilityFocused(_ value: Bool) { if value { window?.makeFirstResponder(self) } }
    // Some providers omit the live editor from Children; its Parent still
    // leads to this same field, sometimes through another text-field proxy.
    override func accessibilityChildren() -> [Any]? { mode.hasPrefix("reverse") ? [] : subviews }
    override func keyDown(with event: NSEvent) {
        guard editor == nil, let chars = event.characters, !chars.isEmpty else {
            super.keyDown(with: event)
            return
        }
        let outer = NSView(frame: bounds), inner = NSView(frame: bounds)
        for view in [outer, inner] {
            view.setAccessibilityElement(true)
            view.setAccessibilityRole(.group)
        }
        if mode == "reverse-nested" { outer.setAccessibilityRole(.textField) }
        outer.addSubview(inner)
        if mode.hasSuffix("sibling"), let region = superview?.superview as? EditingRegion {
            region.addSubview(outer)
            if mode != "unselected-sibling" { region.selectedEditor = outer }
            if mode == "changed-sibling" {
                region.subviews.first(where: { $0.accessibilityIdentifier() == "existing-content-" + mode })?
                    .removeFromSuperview()
            }
        } else {
            addSubview(outer)
        }
        let field = HandoffField(string: chars)
        field.omitParent = mode == "forward"
        if mode == "conflict" { field.conflictingParent = other }
        field.setAccessibilityIdentifier(mode + "-editor")
        field.frame = NSRect(x: 0, y: 0, width: bounds.width, height: 28)
        inner.addSubview(field)
        editor = field
        if mode == "ambiguous" {
            let second = NSTextField(string: "untouched")
            second.setAccessibilityIdentifier("ambiguous-second")
            second.frame = NSRect(x: 0, y: 30, width: bounds.width, height: 28)
            inner.addSubview(second)
        }
        window?.makeFirstResponder(field)
        field.currentEditor()?.selectedRange = NSRange(location: chars.utf16.count, length: 0)
        if mode == "hijack", let other { window?.makeFirstResponder(other) }
        onChange?()
    }
    override func draw(_ dirtyRect: NSRect) {
        if editor == nil {
            ("Type to create " + mode + " editor" as NSString).draw(
                at: NSPoint(x: 3, y: 8), withAttributes: [.foregroundColor: NSColor.labelColor])
        }
    }
}

@MainActor final class HandoffDelegate: NSObject, NSApplicationDelegate {
    var window: NSWindow!
    var hosts = [EditorHost]()
    let other = NSTextField(string: "untouched")
    var directory: URL { Bundle.main.bundleURL.deletingLastPathComponent() }
    func record() {
        var values = Dictionary(uniqueKeysWithValues: hosts.map { ($0.mode, $0.editor?.stringValue ?? "") })
        values["other"] = other.stringValue
        try! JSONSerialization.data(withJSONObject: values, options: [.sortedKeys]).write(
            to: directory.appendingPathComponent("values.json"))
    }
    func applicationDidFinishLaunching(_ notification: Notification) {
        try! String(getpid()).write(
            to: directory.appendingPathComponent("fixture.pid"), atomically: true, encoding: .utf8)
        window = NSWindow(
            contentRect: NSRect(x: 0, y: 0, width: 560, height: 860), styleMask: [.titled, .closable],
            backing: .buffered, defer: false)
        window.title = "Editor handoff"
        window.tabbingMode = .disallowed
        window.isReleasedWhenClosed = false
        other.frame = NSRect(x: 20, y: 20, width: 510, height: 28)
        other.setAccessibilityIdentifier("other")
        window.contentView?.addSubview(other)
        for (i, mode) in [
            "nested", "forward", "ambiguous", "hijack", "conflict", "sibling", "unselected-sibling", "changed-sibling",
            "reverse", "reverse-nested"
        ].enumerated() {
            let frame = NSRect(x: 20, y: 780 - Double(i) * 75, width: 510, height: 65)
            let host = EditorHost(mode: mode, frame: frame)
            host.other = other
            host.onChange = { [weak self] in self?.record() }
            hosts.append(host)
            if mode.hasSuffix("sibling") {
                let region = EditingRegion(frame: frame), collection = EditingCollection(frame: region.bounds)
                region.setAccessibilityElement(true)
                region.setAccessibilityRole(.group)
                collection.setAccessibilityElement(true)
                collection.setAccessibilityRole(.table)
                collection.host = host
                host.frame = collection.bounds
                collection.addSubview(host)
                region.addSubview(collection)
                // A collection region may already contain sibling content,
                // such as an embedded chart. It is not the newly created editor.
                let decoration = NSView(frame: NSRect(x: 400, y: 35, width: 80, height: 20))
                decoration.setAccessibilityElement(true)
                decoration.setAccessibilityRole(.group)
                decoration.setAccessibilityIdentifier("existing-content-" + mode)
                region.addSubview(decoration)
                window.contentView?.addSubview(region)
            } else {
                window.contentView?.addSubview(host)
            }
        }
        NotificationCenter.default.addObserver(forName: NSControl.textDidChangeNotification, object: nil, queue: .main)
        { [weak self] _ in MainActor.assumeIsolated { self?.record() } }
        window.center()
        window.orderBack(nil)
        window.makeKey()
        window.makeMain()
        window.makeFirstResponder(other)
        record()
    }
}
let application = NSApplication.shared
application.setActivationPolicy(.regular)
let delegate = HandoffDelegate()
application.delegate = delegate
application.run()
