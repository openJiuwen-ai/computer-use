import AppKit

// A disposable, ordinary AppKit UI. The driver must discover its structure
// through the same public accessibility interface used for any application.
@MainActor
final class ObservationProbe: NSObject, NSApplicationDelegate, NSTextFieldDelegate, NSTableViewDataSource,
    NSTableViewDelegate
{
    var documents = [NSWindow]()
    var sheet: NSWindow?
    var panel: NSPanel?
    var fields = [String: NSTextField]()
    var log: FileHandle!
    var table: NSTableView!
    var lastState = ""
    let proof = UUID().uuidString
    let rows = [["A", "same", ""], ["B", "same", "value"], ["C", "", "value"]]

    func record() {
        let state: [String: Any] = [
            "fields": fields.mapValues(\.stringValue), "proof": proof, "sheet": sheet?.isVisible == true,
            "panel": panel?.isVisible == true, "selectedRow": table?.selectedRow ?? -1,
            "foreground": NSWorkspace.shared.frontmostApplication?.bundleIdentifier ?? "unknown"
        ]
        let data = try! JSONSerialization.data(withJSONObject: state, options: [.sortedKeys])
        let value = String(decoding: data, as: UTF8.self)
        if value != lastState {
            try? log.write(contentsOf: data + Data([10]))
            lastState = value
        }
    }

    func group(_ title: String, frame: NSRect, in parent: NSView) -> NSView {
        let box = NSBox(frame: frame)
        box.title = title
        box.setAccessibilityLabel(title)
        parent.addSubview(box)
        return box.contentView!
    }

    func field(_ key: String, label: String, value: String, at frame: NSRect, in parent: NSView) {
        let input = NSTextField(string: value)
        input.frame = frame
        input.setAccessibilityLabel(label)
        input.setAccessibilityIdentifier("implementation-" + key)
        input.delegate = self
        fields[key] = input
        parent.addSubview(input)
    }

    func button(_ title: String, _ action: Selector, at frame: NSRect, in parent: NSView) {
        let button = NSButton(title: title, target: self, action: action)
        button.frame = frame
        parent.addSubview(button)
    }

    func window(_ title: String, frame: NSRect) -> NSWindow {
        let window = NSWindow(
            contentRect: frame, styleMask: [.titled, .closable, .resizable], backing: .buffered, defer: false)
        window.title = title
        window.isReleasedWhenClosed = false
        return window
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        let url = Bundle.main.bundleURL.deletingPathExtension().appendingPathExtension("events.jsonl")
        FileManager.default.createFile(atPath: url.path, contents: nil)
        log = try! FileHandle(forWritingTo: url)
        let menu = NSMenu()
        let item = NSMenuItem()
        item.submenu = NSMenu()
        item.submenu!.addItem(withTitle: "Quit", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        menu.addItem(item)
        let optionsItem = NSMenuItem(title: "Options", action: nil, keyEquivalent: "")
        let options = NSMenu(title: "Options")
        options.autoenablesItems = false
        for (title, state) in [
            ("Enabled option", NSControl.StateValue.on), ("Disabled option", .off), ("Mixed option", .mixed)
        ] {
            let option = NSMenuItem(title: title, action: #selector(toggleOption(_:)), keyEquivalent: "")
            option.target = self
            option.state = state
            options.addItem(option)
        }
        optionsItem.submenu = options
        menu.addItem(optionsItem)
        NSApp.mainMenu = menu

        let first = window("Observation document", frame: NSRect(x: 80, y: 100, width: 760, height: 520))
        let root = first.contentView!
        let profile = group("Profile", frame: NSRect(x: 20, y: 380, width: 340, height: 110), in: root)
        field("profile", label: "Name", value: "Alice", at: NSRect(x: 10, y: 35, width: 260, height: 25), in: profile)
        let delivery = group("Delivery", frame: NSRect(x: 390, y: 380, width: 340, height: 110), in: root)
        field("delivery", label: "Name", value: "Bob", at: NSRect(x: 10, y: 35, width: 260, height: 25), in: delivery)

        let scroll = NSScrollView(frame: NSRect(x: 20, y: 180, width: 340, height: 185))
        scroll.hasVerticalScroller = true
        table = NSTableView()
        for (id, title) in ["Key", "First", "Second"].enumerated() {
            let column = NSTableColumn(identifier: NSUserInterfaceItemIdentifier(String(id)))
            column.title = title
            column.width = 95
            table.addTableColumn(column)
        }
        table.dataSource = self
        table.delegate = self
        table.setAccessibilityLabel("Records")
        scroll.documentView = table
        root.addSubview(scroll)

        let bodyScroll = NSScrollView(frame: NSRect(x: 390, y: 180, width: 340, height: 185))
        bodyScroll.hasVerticalScroller = true
        let body = NSTextView(frame: NSRect(x: 0, y: 0, width: 320, height: 185))
        body.isEditable = false
        body.string = (0..<220).map {
            $0 == 120
                ? "Paragraph 120: verification code \(proof).\n"
                : "Paragraph \($0): an observable document keeps punctuation, whitespace and complete values.\n"
        }.joined()
        body.setAccessibilityLabel("Document text")
        bodyScroll.documentView = body
        root.addSubview(bodyScroll)
        button("Open sheet", #selector(openSheet), at: NSRect(x: 20, y: 110, width: 140, height: 30), in: root)
        button("Open panel", #selector(openPanel), at: NSRect(x: 180, y: 110, width: 140, height: 30), in: root)
        let shared = group("Shared help", frame: NSRect(x: 20, y: 20, width: 710, height: 70), in: root)
        for index in 1...3 {
            let key = "shared\(index)"
            field(
                key, label: "Field \(index)", value: "Value \(index)",
                at: NSRect(x: 10 + (index - 1) * 220, y: 15, width: 190, height: 25), in: shared)
            fields[key]!.setAccessibilityHelp(
                "Enter a value here. This instruction belongs to each directly contained field.")
        }
        let second = window("Observation document", frame: NSRect(x: 870, y: 160, width: 300, height: 180))
        field(
            "other", label: "Name", value: "Other document", at: NSRect(x: 20, y: 90, width: 240, height: 25),
            in: second.contentView!)
        documents = [first, second]
        second.orderBack(nil)
        first.orderBack(nil)
        first.makeKey()
        first.makeMain()
        first.makeFirstResponder(fields["profile"])
        Timer.scheduledTimer(withTimeInterval: 0.1, repeats: true) { [weak self] _ in
            MainActor.assumeIsolated { self?.record() }
        }
        record()
    }

    @objc func openSheet() {
        guard sheet == nil else { return }
        let dialog = window("Export options", frame: NSRect(x: 0, y: 0, width: 440, height: 230))
        let settings = group(
            "File settings", frame: NSRect(x: 20, y: 80, width: 400, height: 130), in: dialog.contentView!)
        field(
            "filename", label: "Name", value: "report.txt", at: NSRect(x: 10, y: 50, width: 320, height: 25),
            in: settings)
        button(
            "Cancel export", #selector(closeSheet), at: NSRect(x: 230, y: 25, width: 180, height: 30),
            in: dialog.contentView!)
        sheet = dialog
        documents[0].beginSheet(dialog) { [weak self] _ in self?.record() }
        record()
    }

    @objc func closeSheet() {
        if let sheet {
            documents[0].endSheet(sheet)
            sheet.orderOut(nil)
        }
        sheet = nil
        record()
    }

    @objc func openPanel() {
        guard panel == nil else { return }
        let window = NSPanel(
            contentRect: NSRect(x: 350, y: 300, width: 300, height: 150),
            styleMask: [.titled, .closable, .utilityWindow], backing: .buffered, defer: false)
        window.title = "Inspector"
        window.isReleasedWhenClosed = false
        // This scenario is a persistent inspector, including while its app is
        // inactive; NSPanel otherwise hides itself on deactivation by default.
        window.hidesOnDeactivate = false
        let label = NSTextField(labelWithString: "A non-modal floating panel")
        label.frame = NSRect(x: 20, y: 85, width: 260, height: 24)
        window.contentView!.addSubview(label)
        button(
            "Close inspector", #selector(closePanel), at: NSRect(x: 20, y: 30, width: 170, height: 30),
            in: window.contentView!)
        panel = window
        window.orderBack(nil)
        window.makeKey()
        record()
    }

    @objc func toggleOption(_ sender: NSMenuItem) { sender.state = sender.state == .on ? .off : .on }
    @objc func closePanel() {
        panel?.close()
        panel = nil
        record()
    }
    func numberOfRows(in tableView: NSTableView) -> Int { rows.count }
    func tableView(_ tableView: NSTableView, objectValueFor tableColumn: NSTableColumn?, row: Int) -> Any? {
        rows[row][Int(tableColumn!.identifier.rawValue)!]
    }
}

let application = NSApplication.shared
application.setActivationPolicy(.regular)
let delegate = ObservationProbe()
application.delegate = delegate
application.run()
