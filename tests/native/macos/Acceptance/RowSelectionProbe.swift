import AppKit

// Disposable selection oracle. Writes and mouse callbacks are recorded
// independently so a property write cannot be mistaken for a physical click.
@MainActor final class SelectionRow: NSView {
    let index: Int
    unowned let host: SelectionDelegate
    var selected = false
    init(_ index: Int, host: SelectionDelegate) {
        self.index = index
        self.host = host
        super.init(frame: NSRect(x: 0, y: 180 - index * 60, width: 420, height: 56))
        if index == 1 {
            let button = NSButton(title: "Inner button", target: host, action: #selector(SelectionDelegate.inner))
            button.frame = NSRect(x: 270, y: 12, width: 130, height: 30)
            button.setAccessibilityIdentifier("selection-inner-button")
            addSubview(button)
        }
    }
    required init?(coder: NSCoder) { fatalError() }
    override func isAccessibilityElement() -> Bool { true }
    override func accessibilityRole() -> NSAccessibility.Role? { .row }
    override func accessibilityLabel() -> String? { "Selection row \(index)" }
    override func accessibilityIdentifier() -> String { "selection-row-\(index)" }
    override func accessibilityFrame() -> NSRect { host.mode == "no-geometry" ? .zero : super.accessibilityFrame() }
    override func isAccessibilitySelected() -> Bool { selected }
    override func setAccessibilitySelected(_ value: Bool) {
        host.writes.append("AXSelected:\(index)")
        selected = value
        host.page = "Row \(index)"
        host.save()
    }
    override func mouseDown(with event: NSEvent) {
        host.downs += 1
        host.save()
    }
    override func mouseUp(with event: NSEvent) {
        host.ups += 1
        for row in host.rows { row.selected = row === self }
        host.page = "Row \(index)"
        host.save()
    }
    override func acceptsFirstMouse(for event: NSEvent?) -> Bool { true }
    override func draw(_ dirtyRect: NSRect) {
        (selected ? NSColor.selectedContentBackgroundColor : NSColor.controlBackgroundColor).setFill()
        bounds.fill()
        ("Selection row \(index)" as NSString).draw(
            at: NSPoint(x: 16, y: 18),
            withAttributes: [.font: NSFont.systemFont(ofSize: 16), .foregroundColor: NSColor.labelColor])
    }
}

@MainActor final class SelectionOutline: NSView {
    unowned let host: SelectionDelegate
    init(host: SelectionDelegate) {
        self.host = host
        super.init(frame: NSRect(x: 20, y: 80, width: 420, height: 240))
    }
    required init?(coder: NSCoder) { fatalError() }
    override func isAccessibilityElement() -> Bool { true }
    override func accessibilityRole() -> NSAccessibility.Role? { .outline }
    override func accessibilityLabel() -> String? { "Selection test outline" }
    override func accessibilityChildren() -> [Any]? { host.rows }
    override func accessibilityRows() -> [Any]? { host.rows }
    override func accessibilitySelectedRows() -> [Any]? { host.rows.filter(\.selected) }
    override func isAccessibilitySelectorAllowed(_ selector: Selector) -> Bool {
        if selector == #selector(setAccessibilitySelectedRows(_:)), host.mode == "direct" { return false }
        return super.isAccessibilitySelectorAllowed(selector)
    }
    override func setAccessibilitySelectedRows(_ value: [Any]?) {
        host.writes.append("AXSelectedRows")
        if host.mode != "noop" {
            let chosen = value?.compactMap { $0 as? SelectionRow } ?? []
            for row in host.rows { row.selected = chosen.contains { $0 === row } }
            host.page = chosen.map { "Row \($0.index)" }.joined(separator: ",")
        }
        host.save()
    }
}

@MainActor final class SelectionDelegate: NSObject, NSApplicationDelegate {
    let folder = URL(fileURLWithPath: CommandLine.arguments[1])
    var window: NSWindow!
    var rows = [SelectionRow]()
    var status: NSTextField!
    var mode = "normal", page = "Initial"
    var writes = [String](), downs = 0, ups = 0, buttons = 0, sequence = 0
    func applicationDidFinishLaunching(_ notification: Notification) {
        window = NSWindow(
            contentRect: NSRect(x: 0, y: 0, width: 470, height: 360), styleMask: [.titled, .closable],
            backing: .buffered, defer: false)
        window.title = "Computer Use Row Selection Probe"
        let outline = SelectionOutline(host: self)
        rows = (0..<3).map { SelectionRow($0, host: self) }
        rows[0].selected = true
        rows.forEach { outline.addSubview($0) }
        window.contentView!.addSubview(outline)
        status = NSTextField(labelWithString: "Initial")
        status.frame = NSRect(x: 20, y: 20, width: 430, height: 45)
        window.contentView!.addSubview(status)
        window.center()
        window.makeKeyAndOrderFront(nil)
        NSApp.activate()
        Timer.scheduledTimer(withTimeInterval: 0.1, repeats: true) { [weak self] _ in
            MainActor.assumeIsolated { self?.command() }
        }
        save()
    }
    @objc func inner() {
        buttons += 1
        save()
    }
    func command() {
        guard let data = try? Data(contentsOf: folder.appendingPathComponent("command.json")),
            let value = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
            let next = value["sequence"] as? Int, next != sequence
        else { return }
        sequence = next
        mode = value["mode"] as? String ?? "normal"
        let selected = value["selected"] as? [Int] ?? []
        for row in rows { row.selected = selected.contains(row.index) }
        page = "Setup \(selected)"
        writes = []
        downs = 0
        ups = 0
        buttons = 0
        save()
    }
    func save() {
        rows.forEach { $0.needsDisplay = true }
        status?.stringValue = "\(page) | AX writes \(writes.count) | mouse \(downs)/\(ups) | inner \(buttons)"
        let value: [String: Any] = [
            "pid": getpid(), "sequence": sequence, "mode": mode, "page": page,
            "selected": rows.filter(\.selected).map(\.index), "writes": writes, "downs": downs, "ups": ups,
            "buttons": buttons, "frontmostPID": NSWorkspace.shared.frontmostApplication?.processIdentifier ?? 0
        ]
        if let data = try? JSONSerialization.data(withJSONObject: value, options: [.sortedKeys]) {
            try? data.write(to: folder.appendingPathComponent("state.json"), options: .atomic)
        }
    }
}

let app = NSApplication.shared
app.setActivationPolicy(.regular)
let delegate = SelectionDelegate()
app.delegate = delegate
app.run()
