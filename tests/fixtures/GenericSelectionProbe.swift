import AppKit

// A custom collection exposes selection through its parent, with no standard
// row role or press action. Its actual displayed page is the test oracle.
@MainActor final class SelectionItem: NSView {
    weak var collection: ItemCollection?
    let index: Int
    init(_ index: Int, collection: ItemCollection) {
        self.index = index
        self.collection = collection
        super.init(frame: NSRect(x: 20 + index * 95, y: 80, width: 90, height: 35))
        setAccessibilityElement(true)
    }
    required init?(coder: NSCoder) { fatalError() }
    override func accessibilityRole() -> NSAccessibility.Role? { collection?.itemRole ?? .unknown }
    override func accessibilityLabel() -> String? { "Card \(index + 1)" }
    override func isAccessibilitySelected() -> Bool { collection?.selected == index }
    override func accessibilityParent() -> Any? { collection }
    override func isAccessibilitySelectorAllowed(_ selector: Selector) -> Bool {
        if selector == #selector(setAccessibilityFocused(_:)) || selector == #selector(setAccessibilitySelected(_:)) {
            return false
        }
        return super.isAccessibilitySelectorAllowed(selector)
    }
    override func draw(_ dirtyRect: NSRect) {
        (isAccessibilitySelected() ? NSColor.selectedContentBackgroundColor : NSColor.controlBackgroundColor).setFill()
        bounds.fill()
        ("Card \(index + 1)" as NSString).draw(
            at: NSPoint(x: 8, y: 8), withAttributes: [.foregroundColor: NSColor.labelColor])
    }
}
@MainActor final class ItemCollection: NSView {
    var selected = 0
    var writes = 0
    var exposesSelection = true
    var ignoresSelection = false
    var itemRole: NSAccessibility.Role = .unknown
    var onChange: (() -> Void)?
    lazy var items = (0..<3).map { SelectionItem($0, collection: self) }
    override func accessibilityRole() -> NSAccessibility.Role? { .group }
    override func accessibilityChildren() -> [Any]? { items }
    override func accessibilitySelectedChildren() -> [Any]? { [items[selected]] }
    override func setAccessibilitySelectedChildren(_ children: [Any]?) {
        writes += 1
        if !ignoresSelection, let item = children?.first as? SelectionItem, item.collection === self {
            selected = item.index
        }
        for item in items { item.needsDisplay = true }
        needsDisplay = true
        onChange?()
    }
    override func isAccessibilitySelectorAllowed(_ selector: Selector) -> Bool {
        if selector == #selector(setAccessibilityFocused(_:)) { return false }
        if selector == #selector(setAccessibilitySelectedChildren(_:)) { return exposesSelection }
        return super.isAccessibilitySelectorAllowed(selector)
    }
    override func draw(_ dirtyRect: NSRect) {
        ("Displayed page: \(selected + 1)" as NSString).draw(
            at: NSPoint(x: 20, y: 30),
            withAttributes: [.font: NSFont.systemFont(ofSize: 20), .foregroundColor: NSColor.labelColor])
    }
}
@MainActor final class ProbeDelegate: NSObject, NSApplicationDelegate {
    let collection = ItemCollection(frame: NSRect(x: 0, y: 390, width: 340, height: 130))
    let readonly = ItemCollection(frame: NSRect(x: 0, y: 260, width: 340, height: 130))
    let noop = ItemCollection(frame: NSRect(x: 0, y: 130, width: 340, height: 130))
    let editors = ItemCollection(frame: NSRect(x: 0, y: 0, width: 340, height: 130))
    var window: NSWindow!
    let directory = Bundle.main.bundleURL.deletingLastPathComponent()
    func record() {
        let value: [String: Any] = [
            "selected": collection.selected, "writes": collection.writes, "readonly": readonly.selected,
            "readonlyWrites": readonly.writes, "noop": noop.selected, "noopWrites": noop.writes,
            "editorWrites": editors.writes
        ]
        try! JSONSerialization.data(withJSONObject: value).write(
            to: directory.appendingPathComponent("state.json"), options: .atomic)
    }
    func applicationDidFinishLaunching(_ notification: Notification) {
        try! String(getpid()).write(
            to: directory.appendingPathComponent("fixture.pid"), atomically: true, encoding: .utf8)
        window = NSWindow(
            contentRect: NSRect(x: 180, y: 220, width: 340, height: 520), styleMask: [.titled, .closable],
            backing: .buffered, defer: false)
        window.title = "Generic selection fixture"
        window.isReleasedWhenClosed = false
        readonly.exposesSelection = false
        noop.ignoresSelection = true
        editors.itemRole = .textArea
        for (group, name) in [
            (collection, "Selectable cards"), (readonly, "Readonly cards"), (noop, "No-op cards"),
            (editors, "Text editors")
        ] {
            group.setAccessibilityElement(true)
            group.setAccessibilityLabel(name)
            window.contentView?.addSubview(group)
            for item in group.items { group.addSubview(item) }
            group.onChange = { [weak self] in self?.record() }
        }
        window.orderBack(nil)
        record()
    }
}
let application = NSApplication.shared
application.setActivationPolicy(.regular)
let delegate = ProbeDelegate()
application.delegate = delegate
application.run()
