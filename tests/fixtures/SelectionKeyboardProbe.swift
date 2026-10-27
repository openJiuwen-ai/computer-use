import AppKit

// A real NSTextField and NSTableView share one ordinary AppKit responder chain.
// The table's AX selection setters deliberately do ONLY selection, as the AX
// contract permits. Keyboard focus is a separate capability on the collection.
@MainActor final class SelectionApplication: NSApplication {
    override func accessibilityApplicationFocusedUIElement() -> Any? {
        guard let probe = delegate as? SelectionDelegate else {
            return super.accessibilityApplicationFocusedUIElement()
        }
        // AppKit's single-celled NSControl can be ignored in the AX tree: the
        // visible text field is its NSTextFieldCell, not the NSTextField view.
        // Return AppKit's unignored object for the actual first responder's
        // control, rather than inventing a second AX identity for that control.
        if probe.focus == "input" { return NSAccessibility.unignoredDescendant(of: probe.input) }
        if probe.focus == "table" { return NSAccessibility.unignoredDescendant(of: probe.table) }
        return super.accessibilityApplicationFocusedUIElement()
    }
    override func sendEvent(_ event: NSEvent) {
        let probe = delegate as? SelectionDelegate
        if [.keyDown, .keyUp].contains(event.type) {
            probe?.record(
                "key-before",
                extra: [
                    "eventType": event.type.rawValue, "keyCode": event.keyCode, "eventWindow": event.windowNumber,
                    "eventFlags": event.modifierFlags.rawValue
                ])
        }
        super.sendEvent(event)
        if [.keyDown, .keyUp].contains(event.type) {
            probe?.record(
                "key-after",
                extra: ["eventType": event.type.rawValue, "keyCode": event.keyCode, "eventWindow": event.windowNumber])
        }
    }
}

@MainActor final class SelectionWindow: NSWindow {
    weak var recorder: SelectionDelegate?
    override func setAccessibilityMain(_ value: Bool) {
        super.setAccessibilityMain(value)
        if value { makeKey() }
        recorder?.record("AXMain")
    }
    override func makeFirstResponder(_ responder: NSResponder?) -> Bool {
        let result = super.makeFirstResponder(responder)
        recorder?.record("first-responder", extra: ["accepted": result])
        return result
    }
}

// Stable AX row objects avoid depending on AppKit's private row proxy classes.
// Visual layout, mouse selection, and keyboard extension remain NSTableView's.
@MainActor final class SelectionRow: NSView, @preconcurrency NSAccessibilityRow {
    weak var table: SelectionTable?
    let index: Int
    init(index: Int, table: SelectionTable) {
        self.index = index
        self.table = table
        super.init(frame: .zero)
        setAccessibilityElement(true)
        setAccessibilityRole(.row)
        setAccessibilityIdentifier("selection-row-\(index + 1)")
        setAccessibilityLabel("Row \(index + 1)")
        setAccessibilityParent(table)
    }
    required init?(coder: NSCoder) { fatalError("init(coder:) not implemented") }
    override func accessibilityIndex() -> Int { index }
    override func accessibilityIdentifier() -> String { "selection-row-\(index + 1)" }
    override func accessibilityParent() -> Any? { table }
    override func accessibilityWindow() -> Any? { table?.window }
    override func accessibilityTopLevelUIElement() -> Any? { table?.window }
    override func accessibilityFrame() -> NSRect {
        guard let table, let window = table.window else { return .zero }
        return window.convertToScreen(table.convert(table.rect(ofRow: index), to: nil))
    }
    override func isAccessibilitySelected() -> Bool { table?.selectedRowIndexes.contains(index) ?? false }
    override func setAccessibilitySelected(_ value: Bool) {
        guard let table else { return }
        if value {
            table.selectRowIndexes(IndexSet(integer: index), byExtendingSelection: false)
        } else {
            table.deselectRow(index)
        }
        table.recorder?.record("AXSelected", extra: ["row": index, "selected": value])
    }
    override func isAccessibilitySelectorAllowed(_ selector: Selector) -> Bool {
        // Rows are selection targets; the collection owns keyboard navigation.
        if selector == #selector(setAccessibilityFocused(_:)) { return false }
        return super.isAccessibilitySelectorAllowed(selector)
    }
}

@MainActor final class SelectionTable: NSTableView {
    weak var recorder: SelectionDelegate?
    lazy var axRows = (0..<6).map { SelectionRow(index: $0, table: self) }
    override func accessibilityChildren() -> [Any]? { axRows }
    override func accessibilityVisibleChildren() -> [Any]? { axRows }
    override func accessibilityRows() -> [any NSAccessibilityRow]? { axRows }
    override func accessibilityVisibleRows() -> [any NSAccessibilityRow]? { axRows }
    override func accessibilitySelectedRows() -> [any NSAccessibilityRow]? { selectedRowIndexes.map { axRows[$0] } }
    override func setAccessibilitySelectedRows(_ rows: [any NSAccessibilityRow]) {
        let indices = rows.compactMap { ($0 as? SelectionRow)?.index }
        selectRowIndexes(IndexSet(indices), byExtendingSelection: false)
        recorder?.record("AXSelectedRows", extra: ["rows": indices])
    }
    override func isAccessibilityFocused() -> Bool { window?.firstResponder === self }
    override func setAccessibilityFocused(_ value: Bool) {
        if value { window?.makeFirstResponder(self) }
        recorder?.record("AXFocused", extra: ["requested": value])
    }
    override func acceptsFirstMouse(for event: NSEvent?) -> Bool { true }
    override func mouseDown(with event: NSEvent) {
        recorder?.record("table-mouse-before", extra: ["eventWindow": event.windowNumber])
        super.mouseDown(with: event)
        recorder?.record("table-mouse-after", extra: ["eventWindow": event.windowNumber])
    }
    override func keyDown(with event: NSEvent) {
        recorder?.record("table-key-before", extra: ["keyCode": event.keyCode])
        super.keyDown(with: event)
        recorder?.record("table-key-after", extra: ["keyCode": event.keyCode])
    }
}

// A small native control models a provider whose writable AXFocused setter
// does not establish a responder. Missing AX geometry intentionally rules out
// the independent pointer-preparation route; this tests a failed preparation,
// not whether a valid mouse fallback can rescue a broken focus provider.
@MainActor final class RefusedSelectionInput: NSView {
    weak var recorder: SelectionDelegate?
    var text = "Refused unchanged"
    override var acceptsFirstResponder: Bool { true }
    override func accessibilityRole() -> NSAccessibility.Role? { .textField }
    override func accessibilityFrame() -> NSRect { .zero }
    override func accessibilityValue() -> Any? { text }
    override func setAccessibilityValue(_ value: Any?) {
        if let value = value as? String {
            text = value
            needsDisplay = true
        }
        recorder?.record("refused-value")
    }
    override func isAccessibilityFocused() -> Bool { window?.firstResponder === self }
    override func setAccessibilityFocused(_ value: Bool) {
        // Record the attempted native preparation; never change the responder.
        recorder?.record("refused-focus", extra: ["requested": value])
    }
    override func keyDown(with event: NSEvent) {
        text += event.characters ?? ""
        needsDisplay = true
        recorder?.record("refused-key", extra: ["keyCode": event.keyCode])
    }
    override func draw(_ dirtyRect: NSRect) {
        NSColor.controlBackgroundColor.setFill()
        bounds.fill()
        ("Refused focus input: \(text)" as NSString).draw(
            at: NSPoint(x: 6, y: 6), withAttributes: [.foregroundColor: NSColor.labelColor])
    }
}

@MainActor
final class SelectionDelegate: NSObject, NSApplicationDelegate, NSTableViewDataSource, NSTableViewDelegate,
    NSTextFieldDelegate
{
    var window: SelectionWindow!
    let input = NSTextField(string: "Before input")
    let table = SelectionTable(frame: .zero)
    let refused = RefusedSelectionInput(frame: NSRect(x: 24, y: 8, width: 412, height: 26))
    var recording = false
    var directory: URL { Bundle.main.bundleURL.deletingLastPathComponent() }
    var focus: String {
        guard let responder = window?.firstResponder else { return "none" }
        if responder === table { return "table" }
        if responder === input || responder === input.currentEditor() { return "input" }
        if responder === refused { return "refused" }
        return String(describing: type(of: responder))
    }
    func record(_ kind: String, extra: [String: Any] = [:]) {
        guard recording else { return }
        var value: [String: Any] = [
            "kind": kind, "timestampMs": Date().timeIntervalSince1970 * 1000, "hostPID": getpid(),
            "window": window.windowNumber, "focus": focus, "input": input.stringValue, "refused": refused.text,
            "selectedRows": Array(table.selectedRowIndexes),
            "foregroundPID": NSWorkspace.shared.frontmostApplication?.processIdentifier ?? 0
        ]
        if let editor = input.currentEditor() {
            value["inputSelection"] = [editor.selectedRange.location, editor.selectedRange.length]
        }
        value.merge(extra) { _, new in new }
        let data = try! JSONSerialization.data(withJSONObject: value, options: [.sortedKeys])
        try! data.write(to: directory.appendingPathComponent("state.json"), options: .atomic)
        let url = directory.appendingPathComponent("events.jsonl")
        if !FileManager.default.fileExists(atPath: url.path) {
            FileManager.default.createFile(atPath: url.path, contents: nil)
        }
        let handle = try! FileHandle(forWritingTo: url)
        try! handle.seekToEnd()
        try! handle.write(contentsOf: data + Data([10]))
        try! handle.close()
    }
    func applicationDidFinishLaunching(_ notification: Notification) {
        try! String(getpid()).write(
            to: directory.appendingPathComponent("fixture.pid"), atomically: true, encoding: .utf8)
        window = SelectionWindow(
            contentRect: NSRect(x: 160, y: 180, width: 460, height: 330), styleMask: [.titled, .closable],
            backing: .buffered, defer: false)
        window.title = "Selection and keyboard focus"
        window.isReleasedWhenClosed = false
        window.tabbingMode = .disallowed
        window.recorder = self
        input.frame = NSRect(x: 24, y: 280, width: 412, height: 28)
        input.setAccessibilityIdentifier("previous-input")
        input.setAccessibilityLabel("Previous input")
        input.delegate = self
        window.contentView?.addSubview(input)
        let column = NSTableColumn(identifier: NSUserInterfaceItemIdentifier("rows"))
        column.title = "Items"
        column.width = 406
        table.addTableColumn(column)
        table.headerView = nil
        table.rowHeight = 32
        table.allowsMultipleSelection = true
        table.allowsEmptySelection = true
        table.dataSource = self
        table.delegate = self
        table.recorder = self
        table.setAccessibilityIdentifier("selection-table")
        table.setAccessibilityLabel("Selection table")
        let scroll = NSScrollView(frame: NSRect(x: 24, y: 40, width: 412, height: 224))
        scroll.borderType = .bezelBorder
        scroll.documentView = table
        scroll.hasVerticalScroller = true
        table.frame = scroll.contentView.bounds
        table.autoresizingMask = [.width]
        window.contentView?.addSubview(scroll)
        table.reloadData()
        refused.setAccessibilityElement(true)
        refused.setAccessibilityLabel("Refused focus input")
        refused.recorder = self
        window.contentView?.addSubview(refused)
        window.orderBack(nil)
        window.makeKey()
        window.makeMain()
        _ = window.makeFirstResponder(input)
        recording = true
        // Prove the fixture's mechanism before any driver action. AX selection
        // must not implicitly repair the first responder on the driver's behalf.
        table.setAccessibilitySelectedRows([table.axRows[0]])
        precondition(table.selectedRowIndexes == IndexSet(integer: 0) && focus == "input")
        record("selection-only-contract")
        table.deselectAll(nil)
        let inputAX = NSAccessibility.unignoredDescendant(of: input) as AnyObject?
        precondition(inputAX != nil, "The real input must have an exposed accessibility object")
        record(
            "ready",
            extra: [
                "axInputIsControl": inputAX === input, "axInputIsCell": inputAX === input.cell,
                "axInputClass": inputAX.map { String(describing: type(of: $0)) } ?? "none"
            ])
        Timer.scheduledTimer(withTimeInterval: 180, repeats: false) { _ in
            MainActor.assumeIsolated { NSApp.terminate(nil) }
        }
    }
    func numberOfRows(in tableView: NSTableView) -> Int { 6 }
    func tableView(_ tableView: NSTableView, objectValueFor tableColumn: NSTableColumn?, row: Int) -> Any? {
        "Row \(row + 1)"
    }
    func tableViewSelectionDidChange(_ notification: Notification) { record("selection-changed") }
    func controlTextDidChange(_ notification: Notification) { record("input-changed") }
    func control(_ control: NSControl, textView: NSTextView, doCommandBy commandSelector: Selector) -> Bool {
        record("input-command", extra: ["command": NSStringFromSelector(commandSelector)])
        return false
    }
}

let application = SelectionApplication.shared
application.setActivationPolicy(.regular)
let delegate = SelectionDelegate()
application.delegate = delegate
application.run()
