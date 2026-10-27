import AppKit

// A real key child window exposes its text view as an AXWindows root, while
// the provider omits AXFocusedWindow and the text view's logical window links.
// Only native parentage connects the active editor to its original document.
@MainActor final class AttachedReceiverApplication: NSApplication {
    var probe: AttachedReceiverDelegate? { delegate as? AttachedReceiverDelegate }
    override func accessibilityFocusedWindow() -> Any? {
        probe?.editing == true ? nil : super.accessibilityFocusedWindow()
    }
    override func accessibilityApplicationFocusedUIElement() -> Any? {
        probe?.editing == true ? probe?.editor : super.accessibilityApplicationFocusedUIElement()
    }
    override func accessibilityWindows() -> [Any]? {
        guard let probe, let document = probe.document, let other = probe.other else {
            return super.accessibilityWindows()
        }
        return (probe.editing ? [probe.editor] : []) + [document, other]
    }
}

@MainActor final class AttachedEditorView: NSTextView {
    weak var probe: AttachedReceiverDelegate?
    override func accessibilityRole() -> NSAccessibility.Role? { .textArea }
    override func accessibilityParent() -> Any? { NSApp }
    override func accessibilityWindow() -> Any? { nil }
    override func accessibilityTopLevelUIElement() -> Any? { nil }
    override func isAccessibilityFocused() -> Bool { window?.firstResponder === self }
    override func insertNewline(_ sender: Any?) { probe?.commitEditor() }
}

@MainActor final class AttachedEditorPanel: NSPanel {
    override var canBecomeKey: Bool { true }
    override var canBecomeMain: Bool { false }
}

@MainActor final class ReceiverDocumentWindow: NSWindow {
    weak var probe: AttachedReceiverDelegate?
    override func setAccessibilityFocused(_ focused: Bool) {
        probe?.windowSelections += 1
        super.setAccessibilityFocused(focused)
    }
    override func setAccessibilityMain(_ main: Bool) {
        probe?.windowSelections += 1
        super.setAccessibilityMain(main)
    }
}

@MainActor final class ReceiverTypeaheadView: NSView {
    weak var probe: AttachedReceiverDelegate?
    override var acceptsFirstResponder: Bool { true }
    override func keyDown(with event: NSEvent) {
        probe?.typeahead += event.characters ?? ""
        probe?.record("document-keyDown")
    }
}

@MainActor final class AttachedReceiverDelegate: NSObject, NSApplicationDelegate, NSWindowDelegate, NSTextViewDelegate {
    let directory: URL
    let editor = AttachedEditorView()
    let guardField = NSTextField(string: "GUARD")
    var document: ReceiverDocumentWindow!
    var other: NSWindow!
    var panel: AttachedEditorPanel!
    var editing = false, finishing = false, pendingOpen = false
    var committed = "", typeahead = ""
    var cancellations = 0, windowSelections = 0

    init(directory: URL) { self.directory = directory }
    func applicationDidFinishLaunching(_ notification: Notification) {
        document = ReceiverDocumentWindow(
            contentRect: NSRect(x: 140, y: 250, width: 460, height: 240), styleMask: [.titled, .closable],
            backing: .buffered, defer: false)
        document.title = "Attached receiver document"
        document.tabbingMode = .disallowed
        document.isReleasedWhenClosed = false
        document.probe = self
        let body = ReceiverTypeaheadView(frame: document.contentView!.bounds)
        body.probe = self
        document.contentView = body
        let open = NSButton(title: "Open attached editor", target: self, action: #selector(openEditor))
        open.frame = NSRect(x: 90, y: 130, width: 260, height: 34)
        body.addSubview(open)
        document.makeFirstResponder(body)
        other = NSWindow(
            contentRect: NSRect(x: 640, y: 250, width: 300, height: 160), styleMask: [.titled, .closable],
            backing: .buffered, defer: false)
        other.title = "Unrelated receiver guard"
        other.tabbingMode = .disallowed
        other.isReleasedWhenClosed = false
        guardField.frame = NSRect(x: 30, y: 60, width: 230, height: 28)
        other.contentView!.addSubview(guardField)
        panel = AttachedEditorPanel(
            contentRect: NSRect(x: 180, y: 300, width: 330, height: 64), styleMask: [.titled, .closable],
            backing: .buffered, defer: false)
        panel.title = "Attached text receiver"
        panel.isReleasedWhenClosed = false
        panel.delegate = self
        editor.frame = NSRect(x: 12, y: 18, width: 305, height: 28)
        editor.setAccessibilityElement(true)
        editor.isRichText = false
        editor.string = "original"
        editor.setAccessibilityIdentifier("attached-editor")
        editor.delegate = self
        editor.probe = self
        panel.contentView!.addSubview(editor)
        other.orderBack(nil)
        document.makeKeyAndOrderFront(nil)
        document.makeMain()
        try! String(getpid()).write(
            to: directory.appendingPathComponent("fixture.pid"), atomically: true, encoding: .utf8)
        record("ready")
        // The old fixture could claim editing while its inactive process had
        // no key window or real text receiver. This is an explicit foreground
        // fixture: establish activation before testing the child receiver.
        NSApp.activate(ignoringOtherApps: true)
        Timer.scheduledTimer(withTimeInterval: 0.05, repeats: true) { _ in
            MainActor.assumeIsolated { self.record("tick") }
        }
        Timer.scheduledTimer(withTimeInterval: 240, repeats: false) { _ in
            MainActor.assumeIsolated { NSApp.terminate(nil) }
        }
    }

    @objc func openEditor() {
        guard !editing else { return }
        guard NSApp.isActive else {
            pendingOpen = true
            NSApp.activate(ignoringOtherApps: true)
            record("awaiting-activation")
            return
        }
        beginEditor()
    }
    func applicationDidBecomeActive(_ notification: Notification) {
        guard document != nil else { return }
        if pendingOpen {
            pendingOpen = false
            beginEditor()
        } else if !editing {
            document.makeKeyAndOrderFront(nil)
            document.makeMain()
            record("active-document")
        }
    }
    private func beginEditor() {
        guard NSApp.isActive else { return }
        editor.string = "original"
        editing = true
        committed = ""
        typeahead = ""
        cancellations = 0
        windowSelections = 0
        document.addChildWindow(panel, ordered: .above)
        panel.makeKeyAndOrderFront(nil)
        panel.makeFirstResponder(editor)
        editor.setSelectedRange(NSRange(location: 0, length: editor.string.utf16.count))
        record("editor-open")
    }
    func windowDidResignKey(_ notification: Notification) {
        guard editing, !finishing, notification.object as? NSWindow === panel else { return }
        cancellations += 1
        editing = false
        document.removeChildWindow(panel)
        panel.orderOut(nil)
        record("editor-cancelled")
    }
    func textDidChange(_ obj: Notification) { record("editor-text") }
    func commitEditor() {
        committed = editor.string
        finishing = true
        editing = false
        document.removeChildWindow(panel)
        panel.orderOut(nil)
        finishing = false
        record("editor-commit")
    }
    func record(_ reason: String) {
        guard document != nil, panel != nil, other != nil else { return }
        let result: [String: Any] = [
            "reason": reason, "pid": getpid(), "editing": editing, "appActive": NSApp.isActive,
            "frontmostPID": NSWorkspace.shared.frontmostApplication?.processIdentifier ?? 0,
            "editorFocused": panel.firstResponder === editor, "panelKey": panel.isKeyWindow, "text": editor.string,
            "guard": guardField.stringValue, "committed": committed, "typeahead": typeahead,
            "cancellations": cancellations, "windowSelections": windowSelections, "document": document.windowNumber,
            "editorWindow": panel.windowNumber, "otherWindow": other.windowNumber,
            "keyWindow": NSApp.keyWindow?.windowNumber ?? 0
        ]
        try! JSONSerialization.data(withJSONObject: result, options: [.sortedKeys]).write(
            to: directory.appendingPathComponent("state.json"), options: .atomic)
    }
}

let app = AttachedReceiverApplication.shared
app.setActivationPolicy(.regular)
let directory =
    CommandLine.arguments.count > 1
    ? URL(fileURLWithPath: CommandLine.arguments[1]) : Bundle.main.bundleURL.deletingLastPathComponent()
let probe = AttachedReceiverDelegate(directory: directory)
app.delegate = probe
app.run()
