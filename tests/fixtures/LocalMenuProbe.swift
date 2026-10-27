import AppKit

@MainActor final class Part: NSView {
    weak var owner: Delegate?
    let primary: Bool
    init(frame: NSRect, primary: Bool) {
        self.primary = primary
        super.init(frame: frame)
        setAccessibilityElement(true)
        setAccessibilityRole(.group)
        setAccessibilityIdentifier(primary ? "compound" : "member")
        setAccessibilityLabel(primary ? "Compound item" : "")
    }
    required init?(coder: NSCoder) { fatalError() }
    override func acceptsFirstMouse(for event: NSEvent?) -> Bool { true }
    override func accessibilityActionNames() -> [NSAccessibility.Action] {
        primary ? [.press, .showMenu] : [.showMenu, .init(rawValue: "AXScrollToVisible")]
    }
    override func accessibilityPerformAction(_ action: NSAccessibility.Action) {
        MainActor.assumeIsolated { owner?.record(action.rawValue) }
    }
    override func rightMouseDown(with event: NSEvent) {
        owner?.record("rightMouseDown")
        let menu = NSMenu(title: "Local context")
        let item = NSMenuItem(title: "Complete probe", action: #selector(Delegate.complete), keyEquivalent: "")
        item.target = owner
        menu.addItem(item)
        NSMenu.popUpContextMenu(menu, with: event, for: self)
    }
    override func draw(_ rect: NSRect) {
        NSColor.controlBackgroundColor.setFill()
        rect.fill()
        ("Right click compound item" as NSString).draw(
            at: NSPoint(x: 10, y: 20), withAttributes: [.foregroundColor: NSColor.labelColor])
    }
}

@MainActor final class Delegate: NSObject, NSApplicationDelegate {
    var window: NSWindow!
    var counts: [String: Int] = [:]
    let output = Bundle.main.bundleURL.deletingLastPathComponent().appendingPathComponent("state.json")
    func record(_ name: String) {
        counts[name, default: 0] += 1
        publish()
    }
    @objc func complete() { record("complete") }
    func publish() {
        let data: [String: Any] = [
            "pid": getpid(), "counts": counts,
            "foreground": NSWorkspace.shared.frontmostApplication?.bundleIdentifier ?? "unknown"
        ]
        try! JSONSerialization.data(withJSONObject: data, options: [.sortedKeys]).write(to: output, options: .atomic)
    }
    func applicationDidFinishLaunching(_ notification: Notification) {
        window = NSWindow(
            contentRect: NSRect(x: 100, y: 100, width: 440, height: 200), styleMask: [.titled, .closable],
            backing: .buffered, defer: false)
        window.title = "Local Menu Probe"
        window.isReleasedWhenClosed = false
        let control = Part(frame: NSRect(x: 30, y: 50, width: 380, height: 90), primary: true)
        let member = Part(frame: NSRect(x: 0, y: 0, width: 380, height: 90), primary: false)
        control.owner = self
        member.owner = self
        control.addSubview(member)
        window.contentView!.addSubview(control)
        window.orderBack(nil)
        publish()
        Timer.scheduledTimer(withTimeInterval: 120, repeats: false) { _ in exit(0) }
    }
}
let app = NSApplication.shared
let delegate = Delegate()
app.delegate = delegate
app.setActivationPolicy(.accessory)
app.run()
