import AppKit

// Some bridges keep the old document's focus until a specific control is
// focused. Selecting AXMain alone does not establish the new responder.
@MainActor final class PreparationApplication: NSApplication {
    // A custom toolkit routes keys to its logical input document. The AX focus
    // and real event recipient derive from the same state, so claiming focus
    // without delivering text there cannot make this fixture pass.
    override func sendEvent(_ event: NSEvent) {
        if [.keyDown, .keyUp].contains(event.type), let probe = delegate as? PreparationDelegate {
            probe.reportedFocus.window?.sendEvent(event)
            probe.record("key")
        } else {
            super.sendEvent(event)
        }
    }
    override func accessibilityApplicationFocusedUIElement() -> Any? {
        guard let field = (delegate as? PreparationDelegate)?.reportedFocus else { return nil }
        return NSAccessibility.unignoredDescendant(of: field)
    }
    override func accessibilityFocusedWindow() -> Any? { (delegate as? PreparationDelegate)?.reportedFocus.window }
}

@MainActor final class PreparationWindow: NSWindow {
    override var canBecomeKey: Bool { true }
    override func setAccessibilityMain(_ value: Bool) { if value { makeMain() } }
}

@MainActor class PreparationField: NSTextField {
    var onFocus: (() -> Void)?
    var preparesNativeFocus = false
    override func setAccessibilityFocused(_ value: Bool) {
        if value {
            if preparesNativeFocus { super.setAccessibilityFocused(value) }
            onFocus?()
        }
    }
}

@MainActor final class NoOpPreparationField: PreparationField {
    var onValue: (() -> Void)?
    override func setAccessibilityValue(_ value: Any?) { onValue?() }
}

@MainActor final class PreparationDelegate: NSObject, NSApplicationDelegate, NSTextFieldDelegate {
    var source: NSWindow!, destination: NSWindow!
    let original = NSTextField(string: "unchanged")
    let input = PreparationField(string: "before")
    let refused = PreparationField(string: "refused unchanged")
    let redirected = PreparationField(string: "redirected unchanged")
    let noOp = NoOpPreparationField(string: "no-op unchanged")
    var reportedFocus: NSTextField { focus ?? original }
    private var focus: NSTextField?
    private var evidence: URL { Bundle.main.bundleURL.deletingLastPathComponent() }
    func record(_ event: String) {
        let record: [String: Any] = [
            "event": event, "original": original.stringValue, "input": input.stringValue,
            "refused": refused.stringValue, "redirected": redirected.stringValue, "noOp": noOp.stringValue,
            "focus": reportedFocus.accessibilityIdentifier(), "keyWindow": NSApp.keyWindow?.title ?? ""
        ]
        let data = try! JSONSerialization.data(withJSONObject: record, options: [.sortedKeys]) + Data([10])
        let file = evidence.appendingPathComponent("events.jsonl")
        if !FileManager.default.fileExists(atPath: file.path) {
            FileManager.default.createFile(atPath: file.path, contents: nil)
        }
        let handle = try! FileHandle(forWritingTo: file)
        try! handle.seekToEnd()
        try! handle.write(contentsOf: data)
        try! handle.close()
    }
    func activate(_ field: NSTextField) {
        field.window?.makeKey()
        field.window?.makeMain()
        field.window?.makeFirstResponder(field)
        focus = field
    }
    func applicationDidFinishLaunching(_ notification: Notification) {
        try! String(getpid()).write(
            to: evidence.appendingPathComponent("fixture.pid"), atomically: true, encoding: .utf8)
        source = PreparationWindow(
            contentRect: NSRect(x: 100, y: 180, width: 470, height: 230), styleMask: [.titled, .closable],
            backing: .buffered, defer: false)
        destination = PreparationWindow(
            contentRect: NSRect(x: 590, y: 180, width: 470, height: 370), styleMask: [.titled, .closable],
            backing: .buffered, defer: false)
        source.title = "Original document"
        destination.title = "Target document"
        for window in [source!, destination!] {
            window.tabbingMode = .disallowed
            window.isReleasedWhenClosed = false
            window.orderBack(nil)
        }
        original.frame = NSRect(x: 20, y: 100, width: 420, height: 30)
        original.setAccessibilityIdentifier("original")
        original.delegate = self
        source.contentView?.addSubview(original)
        for (field, id, y) in [
            (input, "target", 270.0), (refused, "refused", 215.0), (redirected, "redirected", 160.0),
            (noOp, "noop", 105.0)
        ] {
            field.frame = NSRect(x: 20, y: y, width: 420, height: 30)
            field.setAccessibilityIdentifier(id)
            field.delegate = self
            destination.contentView?.addSubview(field)
        }
        input.preparesNativeFocus = true
        input.onFocus = { [weak self] in
            guard let self else { return }
            self.activate(self.input)
            self.record("target-focus")
        }
        refused.onFocus = { [weak self] in self?.record("refused-focus") }
        redirected.onFocus = { [weak self] in
            guard let self else { return }
            self.activate(self.original)
            self.record("redirected-focus")
        }
        // Hide pointer geometry for the two deliberate native refusals: these
        // cases test the no-recipient boundary, not a valid mouse fallback.
        for field in [refused, redirected, noOp] { field.setAccessibilityFrame(.zero) }
        noOp.onValue = { [weak self] in self?.record("noop-value") }
        let reset = NSButton(title: "Reset focus", target: self, action: #selector(resetFocus))
        reset.frame = NSRect(x: 20, y: 30, width: 140, height: 30)
        destination.contentView?.addSubview(reset)
        activate(original)
        record("ready")
        // Observe real control values even when AX editing sends no delegate
        // text-change notification. This oracle never routes or changes input.
        Timer.scheduledTimer(withTimeInterval: 0.1, repeats: true) { _ in
            MainActor.assumeIsolated { self.record("sample") }
        }
    }
    @objc func resetFocus() {
        activate(original)
        record("reset")
    }
    func controlTextDidChange(_ notification: Notification) { record("text") }
}
let application = PreparationApplication.shared
application.setActivationPolicy(.regular)
let delegate = PreparationDelegate()
application.delegate = delegate
application.run()
