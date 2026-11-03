// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit

/// Ordinary table selection remains AppKit-owned. The override records delivery
/// without replacing selection, accessibility roles/actions, or responder logic.
@MainActor final class SelectionTable: NSTableView {
    var mouseDowns = [[String: Any]]()
    var mouseTracking = [[String: Any]]()
    var changed: () -> Void = {}

    override func mouseDown(with event: NSEvent) {
        mouseDowns.append([
            "button": event.buttonNumber, "clickCount": event.clickCount,
            "window": event.windowNumber, "row": row(at: convert(event.locationInWindow, from: nil)),
            "selectedBefore": Array(selectedRowIndexes),
        ])
        let application = NSApp as? RowSelectionApplication
        application?.tableMouseDownDepth += 1
        func tracking(_ phase: String) -> [String: Any] {
            ["phase": phase, "eventNumber": event.eventNumber, "timestamp": event.timestamp,
             "button": event.buttonNumber, "clickCount": event.clickCount, "window": event.windowNumber,
             "uptime": ProcessInfo.processInfo.systemUptime]
        }
        mouseTracking.append(tracking("entered"))
        defer {
            mouseTracking.append(tracking("returned"))
            application?.tableMouseDownDepth -= 1
            changed()
        }
        changed()
        super.mouseDown(with: event)
    }
}

@MainActor final class RowSelectionApplication: NSApplication {
    var inputs = [[String: Any]]()
    var dequeuedInputs = [[String: Any]]()
    var tableMouseDownDepth = 0

    /// Ordinary control tracking may consume an up directly from the queue,
    /// without another sendEvent call. Observe only actual dequeues and return
    /// the original event unchanged; never peek, pump or repost from here.
    override func nextEvent(
        matching mask: NSEvent.EventTypeMask, until expiration: Date?, inMode mode: RunLoop.Mode,
        dequeue deqFlag: Bool
    ) -> NSEvent? {
        let event = super.nextEvent(matching: mask, until: expiration, inMode: mode, dequeue: deqFlag)
        if deqFlag, let event,
            [.leftMouseDown, .leftMouseUp, .rightMouseDown, .rightMouseUp, .otherMouseDown, .otherMouseUp]
                .contains(event.type)
        {
            dequeuedInputs.append([
                "kind": "mouse", "type": event.type.rawValue, "window": event.windowNumber,
                "button": event.buttonNumber, "clickCount": event.clickCount,
                "eventNumber": event.eventNumber, "timestamp": event.timestamp, "mode": mode.rawValue,
                "insideTableMouseDown": tableMouseDownDepth > 0,
            ])
        }
        return event
    }

    override func sendEvent(_ event: NSEvent) {
        if [.leftMouseDown, .leftMouseUp, .rightMouseDown, .rightMouseUp, .otherMouseDown, .otherMouseUp]
            .contains(event.type)
        {
            inputs.append([
                "kind": "mouse", "type": event.type.rawValue, "window": event.windowNumber,
                "button": event.buttonNumber, "clickCount": event.clickCount,
            ])
        } else if [.keyDown, .keyUp, .flagsChanged].contains(event.type) {
            inputs.append([
                "kind": "keyboard", "type": event.type.rawValue, "window": event.windowNumber,
                "code": event.keyCode,
            ])
        }
        super.sendEvent(event)
    }
}

@MainActor final class RowSelectionDelegate: NSObject, NSApplicationDelegate, NSTableViewDataSource,
    NSTableViewDelegate
{
    let folder = Bundle.main.bundleURL.deletingLastPathComponent()
    let names = ["Alpha record", "Beta record", "Gamma record"]
    let table = SelectionTable(frame: NSRect(x: 0, y: 0, width: 400, height: 240))
    let guardField = NSTextField(string: "Unrelated draft")
    let summary = NSTextField(labelWithString: "Selected: Alpha record")
    var window: NSWindow!
    var selectionChanges = [[Int]]()

    func numberOfRows(in tableView: NSTableView) -> Int { names.count }

    func tableView(_ tableView: NSTableView, viewFor tableColumn: NSTableColumn?, row: Int) -> NSView? {
        let field = NSTextField(labelWithString: names[row])
        field.identifier = NSUserInterfaceItemIdentifier("record-cell-\(row)")
        return field
    }

    func tableView(_ tableView: NSTableView, rowViewForRow row: Int) -> NSTableRowView? {
        let view = NSTableRowView()
        view.setAccessibilityIdentifier("selection-row-\(row)")
        view.setAccessibilityLabel(names[row])
        return view
    }

    func tableViewSelectionDidChange(_ notification: Notification) {
        let selected = Array(table.selectedRowIndexes)
        selectionChanges.append(selected)
        summary.stringValue = "Selected: " + selected.map { names[$0] }.joined(separator: ", ")
        record()
    }

    func record() {
        guard let window else { return }
        let cursor = CGEvent(source: nil)?.location
        let rows: [[String: Any]] = names.indices.map { index in
            let row = table.rowView(atRow: index, makeIfNecessary: false)
            return [
                "index": index, "label": names[index],
                "objectID": row.map { String(describing: ObjectIdentifier($0)) } as Any? ?? NSNull(),
            ]
        }
        let value: [String: Any] = [
            "pid": getpid(), "window": window.windowNumber,
            "windowObjectID": String(describing: ObjectIdentifier(window)),
            "tableObjectID": String(describing: ObjectIdentifier(table)),
            "selected": Array(table.selectedRowIndexes), "summary": summary.stringValue,
            "selectionChanges": selectionChanges, "mouseDowns": table.mouseDowns,
            "mouseTracking": table.mouseTracking,
            "inputs": (NSApp as? RowSelectionApplication)?.inputs ?? [], "rows": rows,
            "dequeuedInputs": (NSApp as? RowSelectionApplication)?.dequeuedInputs ?? [],
            "guard": guardField.stringValue,
            "frontmostPID": NSWorkspace.shared.frontmostApplication?.processIdentifier as Any? ?? NSNull(),
            "cursor": cursor.map { [$0.x, $0.y] } as Any? ?? NSNull(),
        ]
        try! JSONSerialization.data(withJSONObject: value, options: [.sortedKeys]).write(
            to: folder.appendingPathComponent("state.json"), options: .atomic)
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        window = NSWindow(
            contentRect: NSRect(x: 190, y: 180, width: 480, height: 380),
            styleMask: [.titled, .closable], backing: .buffered, defer: false)
        window.title = "Record selection probe"
        table.dataSource = self
        table.delegate = self
        table.allowsMultipleSelection = true
        table.allowsEmptySelection = true
        table.rowHeight = 44
        table.headerView = nil
        table.setAccessibilityLabel("Review records")
        table.setAccessibilityIdentifier("selection-table")
        let column = NSTableColumn(identifier: NSUserInterfaceItemIdentifier("record"))
        column.width = 410
        table.addTableColumn(column)
        let scroll = NSScrollView(frame: NSRect(x: 20, y: 110, width: 440, height: 240))
        scroll.documentView = table
        scroll.hasVerticalScroller = true
        guardField.frame = NSRect(x: 20, y: 61, width: 440, height: 28)
        guardField.setAccessibilityLabel("Unrelated draft")
        summary.frame = NSRect(x: 20, y: 20, width: 440, height: 24)
        for view in [scroll, guardField, summary] { window.contentView!.addSubview(view) }
        let menu = NSMenu()
        let application = NSMenuItem(title: "RecordReview", action: nil, keyEquivalent: "")
        application.submenu = NSMenu()
        application.submenu!.addItem(withTitle: "Quit", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        menu.addItem(application)
        NSApp.mainMenu = menu
        table.reloadData()
        table.selectRowIndexes(IndexSet(integer: 0), byExtendingSelection: false)
        table.layoutSubtreeIfNeeded()
        window.orderBack(nil)
        window.makeKey()
        window.makeMain()
        window.makeFirstResponder(guardField)
        selectionChanges = []
        table.changed = { [weak self] in self?.record() }
        record()
        Timer.scheduledTimer(withTimeInterval: 0.05, repeats: true) { _ in MainActor.assumeIsolated { self.record() } }
    }
}

@main enum RowSelectionProbe {
    @MainActor static func main() {
        let app = RowSelectionApplication.shared
        app.setActivationPolicy(.regular)
        let delegate = RowSelectionDelegate()
        app.delegate = delegate
        app.run()
    }
}
