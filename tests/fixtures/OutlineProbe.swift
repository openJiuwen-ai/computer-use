import AppKit

final class OutlineItem: NSObject {
    let name: String
    let children: [OutlineItem]
    init(_ name: String, _ children: [OutlineItem] = []) {
        self.name = name
        self.children = children
    }
}

// Ordinary view-based NSOutlineView; no custom accessibility or input handler.
@MainActor final class OutlineDelegate: NSObject, NSApplicationDelegate, NSOutlineViewDataSource, NSOutlineViewDelegate
{
    let directory = Bundle.main.bundleURL.deletingLastPathComponent()
    let roots = [
        OutlineItem("Project Alpha", [OutlineItem("Sources", [OutlineItem("Main.swift")]), OutlineItem("README.md")]),
        OutlineItem("Project Beta", [OutlineItem("Main.swift")])
    ]
    var window: NSWindow!
    let outline = NSOutlineView()
    let unrelated = NSTextField(string: "Unrelated value")
    func record() {
        let rows = (0..<outline.numberOfRows).map { row in
            ["name": (outline.item(atRow: row) as! OutlineItem).name, "level": outline.level(forRow: row)]
                as [String: Any]
        }
        let selected = (outline.item(atRow: outline.selectedRow) as? OutlineItem)?.name
        let value: [String: Any] = [
            "rows": rows, "selected": selected as Any? ?? NSNull(), "selectedRow": outline.selectedRow,
            "unrelated": unrelated.stringValue
        ]
        try! JSONSerialization.data(withJSONObject: value).write(
            to: directory.appendingPathComponent("state.json"), options: .atomic)
    }
    func applicationDidFinishLaunching(_ notification: Notification) {
        try! String(getpid()).write(
            to: directory.appendingPathComponent("fixture.pid"), atomically: true, encoding: .utf8)
        window = NSWindow(
            contentRect: NSRect(x: 130, y: 160, width: 460, height: 310), styleMask: [.titled, .closable],
            backing: .buffered, defer: false)
        window.title = "Outline Acceptance"
        window.isReleasedWhenClosed = false
        let column = NSTableColumn(identifier: NSUserInterfaceItemIdentifier("Name"))
        column.width = 410
        outline.addTableColumn(column)
        outline.outlineTableColumn = column
        outline.headerView = nil
        outline.rowHeight = 28
        outline.dataSource = self
        outline.delegate = self
        let scroll = NSScrollView(frame: NSRect(x: 20, y: 65, width: 420, height: 225))
        scroll.documentView = outline
        scroll.hasVerticalScroller = true
        window.contentView!.addSubview(scroll)
        unrelated.frame = NSRect(x: 20, y: 20, width: 300, height: 25)
        window.contentView!.addSubview(unrelated)
        outline.reloadData()
        outline.expandItem(nil, expandChildren: true)
        window.orderBack(nil)
        record()
        Timer.scheduledTimer(withTimeInterval: 120, repeats: false) { _ in
            MainActor.assumeIsolated { NSApp.terminate(nil) }
        }
    }
    func outlineView(_ outlineView: NSOutlineView, numberOfChildrenOfItem item: Any?) -> Int {
        (item as? OutlineItem)?.children.count ?? roots.count
    }
    func outlineView(_ outlineView: NSOutlineView, child index: Int, ofItem item: Any?) -> Any {
        ((item as? OutlineItem)?.children ?? roots)[index]
    }
    func outlineView(_ outlineView: NSOutlineView, isItemExpandable item: Any) -> Bool {
        !(item as! OutlineItem).children.isEmpty
    }
    func outlineView(_ outlineView: NSOutlineView, viewFor tableColumn: NSTableColumn?, item: Any) -> NSView? {
        let cell = NSTableCellView(), text = NSTextField(labelWithString: (item as! OutlineItem).name)
        text.frame = NSRect(x: 0, y: 0, width: 300, height: 24)
        cell.addSubview(text)
        cell.textField = text
        return cell
    }
    func outlineViewSelectionDidChange(_ notification: Notification) { record() }
}
let application = NSApplication.shared
application.setActivationPolicy(.regular)
let delegate = OutlineDelegate()
application.delegate = delegate
application.run()
