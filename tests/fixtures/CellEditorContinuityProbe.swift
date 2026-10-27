import AppKit

/// Models a cell whose first key or normal double-click creates a selected
/// sibling text editor; activation modes expose selection as NSNotFound first.
/// AppKit routes real keys; the provider keeps its concrete cell locally
/// focused, as a composite owner, while reporting the new text view as focus.
@MainActor final class CellContinuityApplication: NSApplication {
    override func accessibilityApplicationFocusedUIElement() -> Any? {
        guard let probe = delegate as? CellContinuityDelegate else {
            return super.accessibilityApplicationFocusedUIElement()
        }
        if probe.document?.firstResponder === probe.editor { return probe.editor }
        if probe.document?.firstResponder === probe.guardField.currentEditor() { return probe.guardField }
        return probe.owner
    }
}

@MainActor final class CellContinuityRegion: NSView {
    weak var probe: CellContinuityDelegate?
    override func accessibilityChildren() -> [Any]? { subviews }
    override func accessibilitySelectedChildren() -> [Any]? { probe?.editing == true ? [probe!.editingRoot!] : [] }
}
@MainActor final class CellContinuityTable: NSView {
    weak var probe: CellContinuityDelegate?
    override func accessibilityChildren() -> [Any]? { subviews }
    override func isAccessibilityFocused() -> Bool {
        (probe?.activationMode != true || NSApp.isActive)
            && (probe?.editing == true || window?.firstResponder === probe?.owner)
    }
}
@MainActor final class CellContinuityOwner: NSView {
    weak var probe: CellContinuityDelegate?
    var value = ""
    override var acceptsFirstResponder: Bool { true }
    override func accessibilityRole() -> NSAccessibility.Role? { .textField }
    override func accessibilityValue() -> Any? { value }
    override func setAccessibilityValue(_ newValue: Any?) {
        guard let text = newValue as? String else { return }
        value = text
        probe?.ownerValueWrites += 1
        needsDisplay = true
        probe?.record("owner-value")
    }
    override func accessibilitySelectedTextRange() -> NSRange {
        probe?.activationMode == true ? NSRange(location: NSNotFound, length: 0) : super.accessibilitySelectedTextRange()
    }
    override func setAccessibilitySelectedTextRange(_ range: NSRange) {
        probe?.ownerSelectionWrites += 1
        probe?.record("owner-range-set")
    }
    override func accessibilitySelectedText() -> String? { "" }
    override func setAccessibilitySelectedText(_ text: String?) {
        probe?.ownerSelectionWrites += 1
        probe?.record("owner-selected-text-set")
    }
    override func isAccessibilitySelectorAllowed(_ selector: Selector) -> Bool {
        let selection = [#selector(setAccessibilitySelectedText(_:)), #selector(setAccessibilitySelectedTextRange(_:)),
                         #selector(accessibilitySelectedText), #selector(accessibilitySelectedTextRange)]
        if probe?.activationMode == true {
            if selector == #selector(setAccessibilityValue(_:)) { return false }
            if selection.contains(selector) { return true }
        } else if selection.contains(selector) {
            return false
        }
        return super.isAccessibilitySelectorAllowed(selector)
    }
    override func accessibilityChildren() -> [Any]? { [] }
    override func isAccessibilityFocused() -> Bool {
        (probe?.activationMode != true || NSApp.isActive)
            && (probe?.editing == true || window?.firstResponder === self)
    }
    override func setAccessibilityFocused(_ focused: Bool) { if focused { window?.makeFirstResponder(self) } }
    override func mouseDown(with event: NSEvent) {
        window?.makeFirstResponder(self)
        probe?.ownerMouseDowns += 1
        probe?.record("owner-click")
        if event.clickCount == 2, probe?.activationMode == true { probe?.beginEditor(value) }
    }
    override func keyDown(with event: NSEvent) {
        guard let chars = event.characters, !chars.isEmpty else { return }
        probe?.beginEditor(chars)
    }
    override func draw(_ dirtyRect: NSRect) {
        NSColor.controlBackgroundColor.setFill()
        bounds.fill()
        (value.isEmpty ? "Empty cell: type to edit" : value as NSString).draw(
            at: NSPoint(x: 4, y: 6), withAttributes: [.foregroundColor: NSColor.labelColor])
    }
}
@MainActor final class CellContinuityEditor: NSTextView {
    weak var probe: CellContinuityDelegate?
    override func insertNewline(_ sender: Any?) { probe?.commit() }
}
@MainActor final class CellContinuityDelegate: NSObject, NSApplicationDelegate, NSTextViewDelegate {
    let directory: URL
    let mode: String
    var document: NSWindow!
    let region = CellContinuityRegion(), table = CellContinuityTable(), owner = CellContinuityOwner()
    let guardField = NSTextField(string: "GUARD")
    let editor = CellContinuityEditor()
    var editingRoot: NSView?
    var editing = false, committed = ""
    var ownerMouseDowns = 0, ownerValueWrites = 0, ownerSelectionWrites = 0, editorCreations = 0
    var activationMode: Bool { mode == "selection-activation-empty" || mode == "selection-activation-nonempty" }
    init(directory: URL, mode: String) {
        self.directory = directory
        self.mode = mode
    }
    func applicationDidFinishLaunching(_ notification: Notification) {
        document = NSWindow(
            contentRect: NSRect(x: 140, y: 250, width: 500, height: 300), styleMask: [.titled, .closable],
            backing: .buffered, defer: false)
        document.title = "Cell editor continuity"
        document.tabbingMode = .disallowed
        document.isReleasedWhenClosed = false
        region.frame = NSRect(x: 20, y: 100, width: 450, height: 170)
        table.frame = region.bounds
        owner.frame = NSRect(x: 20, y: 90, width: 270, height: 32)
        for view in [region, table, owner] { view.setAccessibilityElement(true) }
        region.setAccessibilityRole(.group)
        table.setAccessibilityRole(.table)
        owner.setAccessibilityIdentifier("original-cell")
        owner.setAccessibilityLabel("Original cell")
        region.probe = self
        table.probe = self
        owner.probe = self
        if mode == "selection-activation-nonempty" { owner.value = "OLD CONTENT" }
        table.addSubview(owner)
        region.addSubview(table)
        document.contentView!.addSubview(region)
        guardField.frame = NSRect(x: 30, y: 30, width: 350, height: 28)
        guardField.setAccessibilityIdentifier("unrelated-input")
        document.contentView!.addSubview(guardField)
        editor.isRichText = false
        editor.delegate = self
        editor.probe = self
        editor.setAccessibilityElement(true)
        editor.setAccessibilityIdentifier("owned-cell-editor")
        document.makeKeyAndOrderFront(nil)
        document.makeMain()
        document.makeFirstResponder(owner)
        NSApp.activate(ignoringOtherApps: true)
        try! String(getpid()).write(
            to: directory.appendingPathComponent("fixture.pid"), atomically: true, encoding: .utf8)
        record("ready")
        Timer.scheduledTimer(withTimeInterval: 0.05, repeats: true) { _ in
            MainActor.assumeIsolated { self.record("tick") }
        }
        Timer.scheduledTimer(withTimeInterval: 240, repeats: false) { _ in
            MainActor.assumeIsolated { NSApp.terminate(nil) }
        }
    }
    func beginEditor(_ prefix: String) {
        guard !editing else { return }
        editing = true
        editorCreations += 1
        var rootFrame = owner.convert(owner.bounds, to: region).insetBy(dx: 1, dy: 1)
        if activationMode {
            // A selected cell container may be shorter than the editor's text
            // line (as in Calc), and nonempty content can expand it immediately.
            rootFrame.origin.y += rootFrame.height - 10
            rootFrame.size.height = 10
            if mode == "selection-activation-nonempty" { rootFrame.size.width = 400 }
        }
        let root = NSView(frame: rootFrame)
        root.setAccessibilityElement(true)
        root.setAccessibilityRole(.group)
        editor.frame = root.bounds
        if activationMode {
            // NSTextView otherwise shrinks its frame to the text line during
            // layout, defeating a pre-layout `height += 4` overflow fixture.
            // Use a real 14pt view with a 10pt parent, top-aligned in screen
            // coordinates so the editor's AX origin remains inside that parent.
            editor.isVerticallyResizable = false
            editor.minSize = NSSize(width: 0, height: 14)
            editor.frame = NSRect(x: 0, y: root.bounds.height - 14,
                                  width: root.bounds.width, height: 14)
        }
        editor.string = prefix
        root.addSubview(editor)
        root.setAccessibilityChildren([editor])
        root.setAccessibilitySelectedChildren([editor])
        region.addSubview(root)
        editingRoot = root
        document.makeFirstResponder(editor)
        editor.setSelectedRange(NSRange(location: prefix.utf16.count, length: 0))
        if mode == "independent-input" { document.makeFirstResponder(guardField) }
        record("editor-created")
    }
    func commit() {
        committed = editor.string
        owner.value = committed
        editing = false
        editingRoot?.removeFromSuperview()
        editingRoot = nil
        document.makeFirstResponder(owner)
        owner.needsDisplay = true
        record("committed")
    }
    func textDidChange(_ notification: Notification) {
        if mode == "growing-editor", editor.string.utf16.count >= 4, let root = editingRoot {
            // Ordinary layout expansion keeps the same real text receiver and
            // cell owner. It must not be mistaken for a new input target.
            root.setFrameSize(NSSize(width: 400, height: root.frame.height))
            editor.frame = root.bounds
        }
        if mode == "late-independent-input", editor.string.utf16.count >= 4, document.firstResponder === editor {
            // The retained cell editor has already received several keys.
            // Moving focus to an unrelated input must stop the remaining text.
            document.makeFirstResponder(guardField)
        }
        record("editor-text")
    }
    func record(_ reason: String) {
        guard document != nil else { return }
        let values: [String: Any] = [
            "reason": reason, "model": "selected-cell-sibling-v5-activation", "pid": getpid(), "mode": mode,
            "editing": editing, "text": editor.string, "committed": committed, "owner": owner.value,
            "guard": guardField.stringValue, "ownerLocallyFocused": owner.isAccessibilityFocused(),
            "editorFirstResponder": document.firstResponder === editor, "keyWindow": NSApp.keyWindow?.windowNumber ?? 0,
            "document": document.windowNumber, "appActive": NSApp.isActive,
            "frontmostPID": NSWorkspace.shared.frontmostApplication?.processIdentifier ?? 0,
            "ownerMouseDowns": ownerMouseDowns, "ownerValueWrites": ownerValueWrites,
            "ownerSelectionWrites": ownerSelectionWrites, "editorCreations": editorCreations,
            "editorHeight": editor.frame.height, "editingRootHeight": editingRoot?.frame.height ?? 0,
            "ownerWidth": owner.frame.width, "editingRootWidth": editingRoot?.frame.width ?? 0,
            "editorWidth": editor.frame.width
        ]
        try! JSONSerialization.data(withJSONObject: values, options: [.sortedKeys]).write(
            to: directory.appendingPathComponent("state.json"), options: .atomic)
    }
}
let app = CellContinuityApplication.shared
app.setActivationPolicy(.regular)
let directory = URL(fileURLWithPath: CommandLine.arguments[1])
let mode = CommandLine.arguments.count > 2 ? CommandLine.arguments[2] : "normal"
let probe = CellContinuityDelegate(directory: directory, mode: mode)
app.delegate = probe
app.run()
