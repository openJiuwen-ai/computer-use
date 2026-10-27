import AppKit

/// A normal AppKit group with enough real buttons to require several bounded
/// reads. Its order can change without replacing any native button objects.
@MainActor final class ItemList: NSView {
    override var isFlipped: Bool { true }
    var items = [NSButton]()
    let exposesIgnored = Bundle.main.object(forInfoDictionaryKey: "ExposeIgnoredChildren") as? Bool == true
    override func accessibilityChildren() -> [Any]? {
        // NSButton may be an ignored NSControl wrapper around its exposed
        // cell. Match AppKit's public hierarchy in the normal fixture. Keep
        // the malformed provider mode for observing and operating its leaf.
        exposesIgnored ? items : NSAccessibility.unignoredChildren(from: items)
    }
    func arrange() {
        for (index, item) in items.enumerated() { item.frame = NSRect(x: 12, y: index * 30, width: 330, height: 26) }
    }
}

@MainActor final class Delegate: NSObject, NSApplicationDelegate {
    let output = URL(fileURLWithPath: Bundle.main.bundlePath).deletingLastPathComponent()
    var window: NSWindow!
    var list: ItemList!
    var clicked = [Int]()
    var rotations = 0

    func publish() {
        let state: [String: Any] = [
            "clicked": clicked, "rotations": rotations, "order": list.items.map(\.tag),
            "exposesIgnored": list.exposesIgnored,
            "ignoredControls": list.items.filter { !$0.isAccessibilityElement() }.count
        ]
        try! JSONSerialization.data(withJSONObject: state, options: [.sortedKeys]).write(
            to: output.appendingPathComponent("state.json"), options: .atomic)
    }
    @objc func clickItem(_ sender: NSButton) {
        clicked.append(sender.tag)
        publish()
    }
    @objc func rotate(_ sender: NSButton) {
        list.items.append(list.items.removeFirst())
        list.arrange()
        rotations += 1
        publish()
        NSAccessibility.post(element: list!, notification: .layoutChanged)
    }
    func applicationDidFinishLaunching(_ notification: Notification) {
        window = NSWindow(
            contentRect: NSRect(x: 100, y: 100, width: 390, height: 500), styleMask: [.titled, .closable],
            backing: .buffered, defer: false)
        window.title = "Native Child Pages Probe"
        let scroll = NSScrollView(frame: NSRect(x: 10, y: 50, width: 370, height: 440))
        scroll.hasVerticalScroller = true
        list = ItemList(frame: NSRect(x: 0, y: 0, width: 350, height: 9000))
        list.setAccessibilityElement(true)
        list.setAccessibilityRole(.group)
        list.setAccessibilityLabel("Items")
        list.setAccessibilityIdentifier("page-items")
        for index in 0..<300 {
            let button = NSButton(title: "Item \(index)", target: self, action: #selector(clickItem))
            button.tag = index
            button.setAccessibilityIdentifier("page-item-\(index)")
            list.items.append(button)
            list.addSubview(button)
        }
        list.arrange()
        scroll.documentView = list
        window.contentView!.addSubview(scroll)
        let reorder = NSButton(title: "Rotate list", target: self, action: #selector(rotate))
        reorder.setAccessibilityIdentifier("rotate-items")
        reorder.frame = NSRect(x: 15, y: 10, width: 180, height: 30)
        window.contentView!.addSubview(reorder)
        publish()
        window.orderBack(nil)
    }
}

let application = NSApplication.shared
let delegate = Delegate()
application.delegate = delegate
application.setActivationPolicy(.regular)
application.run()
