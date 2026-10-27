import AppKit

@MainActor final class AXReadCanvas: NSView {
    let button = NSButton(title: "Enumerated control", target: nil, action: nil)
    private var actions = 0
    private let output: URL
    private lazy var items: [NSAccessibilityElement] = (0..<130).map { index in
        let item = NSAccessibilityElement()
        item.setAccessibilityRole(.staticText)
        item.setAccessibilityLabel("Read item \(index)")
        item.setAccessibilityIdentifier("ax-read-item-\(index)")
        item.setAccessibilityParent(self)
        return item
    }
    private lazy var attached: NSAccessibilityElement = {
        let item = NSAccessibilityElement()
        item.setAccessibilityRole(NSAccessibility.Role(rawValue: "AXPopover"))
        item.setAccessibilityTitle("Attached audit popover")
        item.setAccessibilityIdentifier("ax-read-attached")
        item.setAccessibilityParent(self)
        item.setAccessibilityWindow(window!)
        item.setAccessibilityFrame(
            NSRect(x: window!.frame.minX + 270, y: window!.frame.minY + 40, width: 120, height: 90))
        return item
    }()

    init(frame: NSRect, output: URL) {
        self.output = output
        super.init(frame: frame)
        button.frame = NSRect(x: 30, y: 100, width: 240, height: 40)
        button.setAccessibilityIdentifier("ax-read-button")
        button.target = self
        button.action = #selector(pressed)
        addSubview(button)
    }
    required init?(coder: NSCoder) { fatalError() }
    @objc private func pressed() {
        actions += 1
        save()
    }
    func save() {
        let data: [String: Any] = [
            "pid": ProcessInfo.processInfo.processIdentifier, "actions": actions, "children": 132, "popoverIndex": 131,
            "window": window!.title
        ]
        try! JSONSerialization.data(withJSONObject: data, options: [.sortedKeys]).write(to: output, options: .atomic)
    }
    override func isAccessibilityElement() -> Bool { true }
    override func accessibilityRole() -> NSAccessibility.Role? { .group }
    override func accessibilityIdentifier() -> String { "ax-read-canvas" }
    override func accessibilityChildren() -> [Any]? { [button] + items + [attached] }
}

@main struct AXReadProbe {
    @MainActor static func main() {
        let app = NSApplication.shared
        app.setActivationPolicy(.regular)
        let window = NSWindow(
            contentRect: NSRect(x: 150, y: 150, width: 420, height: 230), styleMask: [.titled, .closable],
            backing: .buffered, defer: false)
        window.title = "AX Read Probe"
        let canvas = AXReadCanvas(
            frame: NSRect(x: 0, y: 0, width: 420, height: 230), output: URL(fileURLWithPath: CommandLine.arguments[1]))
        window.contentView = canvas
        window.orderFrontRegardless()
        canvas.save()
        withExtendedLifetime((window, canvas)) { app.run() }
    }
}
