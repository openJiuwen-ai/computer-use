import AppKit

@MainActor final class ClickTable: NSTableView {
    weak var recorder: InputClickDelegate?
    override func mouseDown(with event: NSEvent) {
        super.mouseDown(with: event)
        recorder?.record("mouse")
    }
}
@MainActor
final class InputClickDelegate: NSObject, NSApplicationDelegate, NSTableViewDataSource, NSTableViewDelegate,
    NSTextFieldDelegate
{
    let directory = Bundle.main.bundleURL.deletingLastPathComponent()
    var window: NSWindow!
    let table = ClickTable()
    let input = NSTextField(string: "Standalone")
    var editStarts = 0
    func record(_ event: String) {
        let values: [String: Any] = [
            "event": event, "selected": table.selectedRow, "editStarts": editStarts,
            "responder": window.firstResponder.map { String(describing: type(of: $0)) } ?? "none",
            "input": input.stringValue, "pid": getpid()
        ]
        try! JSONSerialization.data(withJSONObject: values).write(
            to: directory.appendingPathComponent("state.json"), options: .atomic)
    }
    func applicationDidFinishLaunching(_ notification: Notification) {
        window = NSWindow(
            contentRect: NSRect(x: 130, y: 170, width: 440, height: 270), styleMask: [.titled, .closable],
            backing: .buffered, defer: false)
        window.title = "Input click acceptance"
        window.isReleasedWhenClosed = false
        let scroll = NSScrollView(frame: NSRect(x: 20, y: 80, width: 400, height: 165))
        scroll.hasVerticalScroller = true
        table.addTableColumn(NSTableColumn(identifier: NSUserInterfaceItemIdentifier("Name")))
        table.tableColumns[0].width = 380
        table.headerView = nil
        table.rowHeight = 34
        table.recorder = self
        table.delegate = self
        table.dataSource = self
        scroll.documentView = table
        input.frame = NSRect(x: 20, y: 25, width: 400, height: 30)
        input.setAccessibilityIdentifier("standalone")
        input.delegate = self
        window.contentView!.addSubview(scroll)
        window.contentView!.addSubview(input)
        window.orderBack(nil)
        window.makeKey()
        window.makeMain()
        window.makeFirstResponder(input)
        table.reloadData()
        record("ready")
        Timer.scheduledTimer(withTimeInterval: 120, repeats: false) { _ in
            MainActor.assumeIsolated { NSApp.terminate(nil) }
        }
    }
    func numberOfRows(in tableView: NSTableView) -> Int { 2 }
    func tableView(_ tableView: NSTableView, viewFor tableColumn: NSTableColumn?, row: Int) -> NSView? {
        let cell = NSTableCellView(frame: NSRect(x: 0, y: 0, width: 380, height: 34))
        let field = NSTextField(string: row == 0 ? "Alpha" : "Beta")
        field.frame = NSRect(x: 6, y: 6, width: 360, height: 22)
        field.isBordered = false
        field.drawsBackground = false
        field.isEditable = true
        field.isSelectable = true
        field.delegate = self
        field.setAccessibilityIdentifier("name-\(row)")
        cell.textField = field
        cell.addSubview(field)
        return cell
    }
    func tableViewSelectionDidChange(_ notification: Notification) { if window != nil { record("selection") } }
    func controlTextDidBeginEditing(_ notification: Notification) {
        editStarts += 1
        record("editing")
    }
    func controlTextDidChange(_ notification: Notification) { record("changed") }
}
let app = NSApplication.shared
app.setActivationPolicy(.regular)
let delegate = InputClickDelegate()
app.delegate = delegate
app.run()
