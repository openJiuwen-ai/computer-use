import AppKit

@MainActor enum Evidence {
    static let directory = Bundle.main.bundleURL.deletingLastPathComponent()
    static func append(_ value: [String: Any]) {
        let path = directory.appendingPathComponent("events.jsonl")
        if !FileManager.default.fileExists(atPath: path.path) {
            FileManager.default.createFile(atPath: path.path, contents: nil)
        }
        guard let data = try? JSONSerialization.data(withJSONObject: value),
            let file = try? FileHandle(forWritingTo: path)
        else { return }
        defer { try? file.close() }
        _ = try? file.seekToEnd()
        try? file.write(contentsOf: data + Data([10]))
    }
}

@MainActor final class ProbeApplication: NSApplication {
    override func sendEvent(_ event: NSEvent) {
        if [.leftMouseDown, .rightMouseDown, .keyDown, .scrollWheel].contains(event.type) {
            var value: [String: Any] = ["type": event.type.rawValue, "window": event.windowNumber]
            // Scroll-only NSEvent properties must not be read on clicks or keys.
            if event.type == .scrollWheel {
                value["dx"] = event.scrollingDeltaX
                value["dy"] = event.scrollingDeltaY
                value["precise"] = event.hasPreciseScrollingDeltas
                value["inverted"] = event.isDirectionInvertedFromDevice
                value["location"] = [event.locationInWindow.x, event.locationInWindow.y]
            }
            Evidence.append(value)
        }
        super.sendEvent(event)
    }
    override func sendAction(_ action: Selector, to target: Any?, from sender: Any?) -> Bool {
        guard let scroller = sender as? NSScroller else { return super.sendAction(action, to: target, from: sender) }
        // Scrollers are peers of the clip view; enclosingScrollView describes
        // document ancestry and need not identify their owning scroll view.
        let region =
            (delegate as? Delegate)?.regions.first {
                $0.value.verticalScroller === scroller || $0.value.horizontalScroller === scroller
            }?.key ?? "unknown"
        let part = scroller.hitPart
        let accepted = super.sendAction(action, to: target, from: sender)
        Evidence.append([
            "kind": "scrollerAction", "region": region, "part": part.rawValue,
            "page": part == .incrementPage || part == .decrementPage, "action": NSStringFromSelector(action),
            "accepted": accepted
        ])
        return accepted
    }

}

@MainActor final class ProbeScrollView: NSScrollView {
    var region = ""
    var supportsNativePages = false
    var exposesScrollArea = true

    override func scrollWheel(with event: NSEvent) {
        Evidence.append([
            "kind": "wheelCallback", "region": region, "dx": event.scrollingDeltaX, "dy": event.scrollingDeltaY
        ])
        super.scrollWheel(with: event)
        (NSApp.delegate as? Delegate)?.record()
    }

    override func accessibilityRole() -> NSAccessibility.Role? {
        exposesScrollArea ? super.accessibilityRole() : .group
    }

    // Only observation is varied. AppKit keeps its real scrollers, page
    // buttons, event delivery and scrolling implementation in both modes.
    override func accessibilityHorizontalScrollBar() -> Any? {
        supportsNativePages ? super.accessibilityHorizontalScrollBar() : nil
    }
    override func accessibilityVerticalScrollBar() -> Any? {
        supportsNativePages ? super.accessibilityVerticalScrollBar() : nil
    }
    override func accessibilityChildren() -> [Any]? {
        let children = super.accessibilityChildren()
        return supportsNativePages ? children : children?.filter { !($0 is NSScroller) }
    }

}

@MainActor final class WheelOnlyScroller: NSScroller { override func accessibilityChildren() -> [Any]? { [] } }

@MainActor final class Grid: NSView {
    override var isFlipped: Bool { true }
    init(name: String, addDisabledAnchor: Bool = false, wideLabels: Bool = false) {
        super.init(frame: NSRect(x: 0, y: 0, width: 1000, height: 2200))
        setAccessibilityRole(.group)
        setAccessibilityLabel(name + " records")
        for row in 0..<60 {
            let label = NSTextField(labelWithString: "\(name) record \(row)")
            label.frame = NSRect(x: 12, y: row * 36, width: wideLabels ? 960 : 290, height: 30)
            if wideLabels {
                label.alignment = .center
                label.setAccessibilityIdentifier(name.lowercased() + "-record-" + String(row))
            }
            addSubview(label)
        }
        if addDisabledAnchor {
            let anchor = NSButton(title: name + " disabled scroll anchor", target: nil, action: nil)
            anchor.frame = NSRect(x: 12, y: 380, width: 290, height: 30)
            anchor.isEnabled = false
            addSubview(anchor)
        }
    }
    required init?(coder: NSCoder) { fatalError() }
    override func draw(_ dirtyRect: NSRect) {
        NSColor.controlBackgroundColor.setFill()
        dirtyRect.fill()
    }
}

@MainActor final class BareCanvas: NSView {
    var offset: CGFloat = 0
    var changed: (() -> Void)?
    override func isAccessibilityElement() -> Bool { false }
    override func accessibilityChildren() -> [Any]? { [] }
    override func draw(_ dirtyRect: NSRect) {
        NSColor.systemTeal.withAlphaComponent(0.25).setFill()
        bounds.fill()
        ("Canvas without AX — offset \(Int(offset))" as NSString).draw(
            at: NSPoint(x: 16, y: 20),
            withAttributes: [.font: NSFont.systemFont(ofSize: 20), .foregroundColor: NSColor.labelColor])
    }
    override func scrollWheel(with event: NSEvent) {
        offset = max(0, offset - event.scrollingDeltaY)
        needsDisplay = true
        changed?()
    }
}

@MainActor final class TableData: NSObject, NSTableViewDataSource {
    let rows: [[String]]
    init(name: String) {
        rows = (0..<80).map { row in (0..<3).map { column in "\(name) row \(row) column \(column)" } }
    }
    func numberOfRows(in tableView: NSTableView) -> Int { rows.count }
    func tableView(_ tableView: NSTableView, objectValueFor tableColumn: NSTableColumn?, row: Int) -> Any? {
        guard let column = tableColumn.flatMap({ Int($0.identifier.rawValue) }) else { return nil }
        return rows[row][column]
    }
}

@MainActor final class Delegate: NSObject, NSApplicationDelegate {
    var window: NSWindow!
    var regions = [String: ProbeScrollView]()
    let canvas = BareCanvas(frame: NSRect(x: 20, y: 60, width: 710, height: 70))
    let status = NSTextField(labelWithString: "")
    var nested: ProbeScrollView?
    var nestedAdded = false
    var texts = [String: NSTextView]()
    var textStorage = [String: NSTextStorage]()
    var tables = [String: NSTableView]()
    var tableData = [String: TableData]()
    var slider: NSSlider?
    var wheelProbe: Bool { Bundle.main.object(forInfoDictionaryKey: "ElementWheelProbe") as? Bool == true }
    var contentMode: String { Bundle.main.object(forInfoDictionaryKey: "ScrollContentProbe") as? String ?? "passive" }

    func document(name: String, nativePages: Bool) -> NSView {
        switch contentMode {
        case "text":
            let storage = NSTextStorage()
            let layout = NSLayoutManager()
            let container = NSTextContainer(containerSize: NSSize(width: 1000, height: CGFloat.greatestFiniteMagnitude))
            container.widthTracksTextView = false
            storage.addLayoutManager(layout)
            layout.addTextContainer(container)
            textStorage[name] = storage
            let text = NSTextView(frame: NSRect(x: 0, y: 0, width: 1000, height: 3600), textContainer: container)
            text.isEditable = true
            text.isSelectable = true
            text.isRichText = false
            text.isVerticallyResizable = false
            text.isHorizontallyResizable = false
            text.textContainerInset = NSSize(width: 12, height: 10)
            text.font = NSFont.monospacedSystemFont(ofSize: 17, weight: .regular)
            text.string = (0..<120).map {
                "\(name) editable paragraph \($0): ordinary AppKit text must remain unchanged while scrolling."
            }.joined(separator: "\n")
            text.setSelectedRange(NSRange(location: 7, length: 11))
            text.setAccessibilityLabel(name + " editable document")
            text.setAccessibilityIdentifier(name.lowercased() + "-text-document")
            texts[name] = text
            return text
        case "table":
            let table = NSTableView(frame: NSRect(x: 0, y: 0, width: 1000, height: 2400))
            table.rowHeight = 30
            table.headerView = nil
            table.allowsMultipleSelection = true
            table.columnAutoresizingStyle = .noColumnAutoresizing
            for index in 0..<3 {
                let column = NSTableColumn(identifier: NSUserInterfaceItemIdentifier(String(index)))
                column.title = "Column \(index)"
                column.width = 330
                table.addTableColumn(column)
            }
            let data = TableData(name: name)
            table.dataSource = data
            tableData[name] = data
            table.reloadData()
            table.selectRowIndexes(IndexSet([7, 9]), byExtendingSelection: false)
            table.setAccessibilityLabel(name + " table document")
            table.setAccessibilityIdentifier(name.lowercased() + "-table-document")
            tables[name] = table
            return table
        default: return Grid(name: name, addDisabledAnchor: nativePages, wideLabels: wheelProbe)
        }
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        try? Data(String(getpid()).utf8).write(to: Evidence.directory.appendingPathComponent("fixture.pid"))
        let menu = NSMenu(), root = NSMenuItem(), commands = NSMenu()
        commands.addItem(withTitle: "Quit", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        root.submenu = commands
        menu.addItem(root)
        NSApp.mainMenu = menu
        window = NSWindow(
            contentRect: NSRect(x: 200, y: 200, width: 750, height: 520), styleMask: [.titled, .closable],
            backing: .buffered, defer: false)
        window.title = "Scroll regions"
        window.isReleasedWhenClosed = false
        for (name, x) in [("Left", 20), ("Right", 390)] {
            let view = ProbeScrollView(frame: NSRect(x: x, y: 155, width: 340, height: 340))
            view.region = name
            let bareBars = Bundle.main.object(forInfoDictionaryKey: "BareScrollBarProbe") as? Bool == true
            view.exposesScrollArea = !bareBars
            view.supportsNativePages =
                bareBars
                || (name == "Left"
                    && Bundle.main.object(forInfoDictionaryKey: "NativePageScrollProbe") as? Bool == true)
            view.hasVerticalScroller = true
            view.hasHorizontalScroller = true
            view.scrollerStyle = .legacy
            if bareBars {
                if name == "Right" { view.verticalScroller = WheelOnlyScroller() }
                view.verticalScroller?.setAccessibilityLabel(name + " vertical scroll bar")
            }
            view.setAccessibilityLabel(name + " list")
            view.setAccessibilityIdentifier(name.lowercased() + "-scroll-region")
            view.documentView = document(name: name, nativePages: view.supportsNativePages)
            view.contentView.postsBoundsChangedNotifications = true
            NotificationCenter.default.addObserver(
                self, selector: #selector(boundsChanged), name: NSView.boundsDidChangeNotification,
                object: view.contentView)
            regions[name] = view
            window.contentView?.addSubview(view)
        }
        canvas.changed = { [weak self] in self?.record() }
        window.contentView?.addSubview(canvas)
        status.frame = NSRect(x: 20, y: 25, width: 535, height: 25)
        window.contentView?.addSubview(status)
        let move = NSButton(title: "Move window", target: self, action: #selector(moveWindow))
        move.frame = NSRect(x: 590, y: 17, width: 140, height: 32)
        window.contentView?.addSubview(move)
        window.orderBack(nil)
        if wheelProbe {
            for view in regions.values {
                view.contentView.scroll(to: NSPoint(x: 200, y: 500))
                view.reflectScrolledClipView(view.contentView)
            }
            _ = Timer.scheduledTimer(withTimeInterval: 0.05, repeats: true) { _ in
                MainActor.assumeIsolated {
                    if self.contentMode == "passive", !self.nestedAdded,
                        FileManager.default.fileExists(
                            atPath: Evidence.directory.appendingPathComponent("add-nested").path)
                    {
                        self.addNestedRegion()
                    }
                    if self.slider == nil,
                        FileManager.default.fileExists(
                            atPath: Evidence.directory.appendingPathComponent("add-slider").path),
                        !FileManager.default.fileExists(
                            atPath: Evidence.directory.appendingPathComponent("remove-slider").path)
                    {
                        self.addSlider()
                    }
                    if let slider = self.slider, slider.superview != nil,
                        FileManager.default.fileExists(
                            atPath: Evidence.directory.appendingPathComponent("remove-slider").path)
                    {
                        slider.removeFromSuperview()
                        Evidence.append(["kind": "fixtureArrangement", "action": "removeSlider"])
                    }
                    self.record()
                }
            }
        }
        record()
    }
    func addSlider() {
        guard let outer = regions["Right"], let document = outer.documentView else { return }
        let visible = outer.documentVisibleRect
        let slider = NSSlider(value: 50, minValue: 0, maxValue: 100, target: nil, action: nil)
        slider.frame = NSRect(x: visible.midX - 100, y: visible.midY - 15, width: 200, height: 30)
        slider.setAccessibilityLabel("Independent value slider")
        slider.setAccessibilityIdentifier("independent-value-slider")
        document.addSubview(slider)
        self.slider = slider
        Evidence.append(["kind": "fixtureArrangement", "action": "addSlider"])
        record()
    }
    func addNestedRegion() {
        // NSTextView/NSTableView own their semantic accessibility children;
        // arbitrary subviews are not a supported observable document subtree.
        // Exercise nested regions in the ordinary NSView container only.
        guard contentMode == "passive" else { return }
        guard let outer = regions["Right"], let document = outer.documentView else { return }
        let visible = outer.documentVisibleRect
        let child = ProbeScrollView(frame: NSRect(x: visible.midX - 100, y: visible.midY - 90, width: 200, height: 180))
        child.region = "Nested"
        child.supportsNativePages = false
        child.hasVerticalScroller = true
        child.hasHorizontalScroller = true
        child.scrollerStyle = .legacy
        child.setAccessibilityLabel("Nested independent list")
        child.setAccessibilityIdentifier("nested-scroll-region")
        child.documentView = Grid(name: "Nested", wideLabels: true)
        child.contentView.postsBoundsChangedNotifications = true
        NotificationCenter.default.addObserver(
            self, selector: #selector(boundsChanged), name: NSView.boundsDidChangeNotification,
            object: child.contentView)
        document.addSubview(child)
        nested = child
        child.contentView.scroll(to: NSPoint(x: 400, y: 300))
        child.reflectScrolledClipView(child.contentView)
        nestedAdded = true
        Evidence.append(["kind": "fixtureArrangement", "action": "addNestedRegion"])
        record()
    }
    @objc func moveWindow() {
        window.setFrameOrigin(NSPoint(x: window.frame.minX + 40, y: window.frame.minY))
        record()
    }
    @objc func boundsChanged(_ notification: Notification) {
        if Bundle.main.object(forInfoDictionaryKey: "DisablePageRouteAfterScroll") as? Bool == true,
            let left = regions["Left"], left.documentVisibleRect.minY > 0
        {
            // A provider can replace/remove this relationship during a page
            // callback. Keep real scrolling intact; only withdraw the route.
            left.supportsNativePages = false
        }
        record()
    }
    func record() {
        guard regions.count == 2 else { return }
        var documents = [String: Any]()
        for (name, text) in texts {
            documents[name] = [
                "text": text.string,
                "selection": text.selectedRanges.map { [$0.rangeValue.location, $0.rangeValue.length] },
                "textSystem": [
                    "storageOwned": text.textStorage === textStorage[name],
                    "layoutAttached": text.layoutManager?.textStorage === textStorage[name],
                    "containerAttached": text.textContainer?.layoutManager === text.layoutManager,
                    "viewAttached": text.textContainer?.textView === text
                ]
            ]
        }
        for (name, table) in tables {
            documents[name] = [
                "rows": tableData[name]!.rows, "selectedRows": Array(table.selectedRowIndexes),
                "selectedColumns": Array(table.selectedColumnIndexes), "editedRow": table.editedRow,
                "editedColumn": table.editedColumn, "rowCount": table.numberOfRows
            ]
        }
        var values: [String: Any] = [
            "canvas": canvas.offset, "nestedAdded": nestedAdded, "contentMode": contentMode, "documents": documents,
            "sliderAdded": slider?.superview != nil, "sliderValue": slider?.doubleValue ?? 50,
            "foreground": NSWorkspace.shared.frontmostApplication?.processIdentifier ?? 0,
            "pointer": [NSEvent.mouseLocation.x, NSEvent.mouseLocation.y],
            "focus": [
                "firstResponder": String(describing: window.firstResponder),
                "keyWindow": NSApp.keyWindow?.windowNumber ?? 0, "mainWindow": NSApp.mainWindow?.windowNumber ?? 0,
                "active": NSApp.isActive
            ]
        ]
        for (name, view) in regions {
            let visible = view.documentVisibleRect
            values[name] = ["x": visible.minX, "y": visible.minY, "width": visible.width, "height": visible.height]
        }
        if let nested {
            let visible = nested.documentVisibleRect
            values["Nested"] = ["x": visible.minX, "y": visible.minY, "width": visible.width, "height": visible.height]
        }
        status.stringValue = regions.keys.sorted().map {
            "\($0): x=\(Int(regions[$0]!.documentVisibleRect.minX)) y=\(Int(regions[$0]!.documentVisibleRect.minY))"
        }.joined(separator: "; ")
        if let bytes = try? JSONSerialization.data(withJSONObject: values) {
            try? bytes.write(to: Evidence.directory.appendingPathComponent("values.json"), options: .atomic)
        }
    }
}

let application = ProbeApplication.shared
let delegate = Delegate()
application.setActivationPolicy(.regular)
application.delegate = delegate
application.run()
