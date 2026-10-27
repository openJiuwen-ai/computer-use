import AppKit

/// AXValue may be a Date or a UI object. Deliberately put each reference before
/// its target in traversal order, including one target outside AXChildren.
@MainActor final class ValueView: NSView {
    var value: Any?
    override func accessibilityValue() -> Any? { value }
}

@MainActor final class Delegate: NSObject, NSApplicationDelegate {
    var window: NSWindow!
    var retained = [NSView]()
    func applicationDidFinishLaunching(_ notification: Notification) {
        window = NSWindow(
            contentRect: NSRect(x: 120, y: 120, width: 420, height: 230), styleMask: [.titled, .closable],
            backing: .buffered, defer: false)
        window.title = "Native Value Probe"
        func value(_ name: String, _ raw: Any, _ y: Int) {
            let view = ValueView(frame: NSRect(x: 20, y: y, width: 380, height: 35))
            view.setAccessibilityElement(true)
            view.setAccessibilityRole(.group)
            view.setAccessibilityLabel(name)
            view.setAccessibilityIdentifier(name)
            view.value = raw
            window.contentView!.addSubview(view)
            retained.append(view)
        }
        value("date-value", Date(timeIntervalSinceReferenceDate: -0.25), 170)
        let choice = NSButton(title: "Referenced choice", target: nil, action: nil)
        choice.frame = NSRect(x: 20, y: 30, width: 180, height: 32)
        choice.setAccessibilityIdentifier("referenced-choice")
        value("object-value", choice, 120)
        window.contentView!.addSubview(choice)
        let outside = NSAccessibilityElement()
        outside.setAccessibilityRole(.button)
        outside.setAccessibilityLabel("Value-only choice")
        outside.setAccessibilityFrame(NSRect(x: 300, y: 120, width: 40, height: 30))
        outside.setAccessibilityParent(window)
        outside.setAccessibilityIdentifier("value-only-choice")
        value("unread-object-value", outside, 70)
        window.orderBack(nil)
        let output = URL(fileURLWithPath: Bundle.main.bundlePath).deletingLastPathComponent()
        try! String(ProcessInfo.processInfo.processIdentifier).write(
            to: output.appendingPathComponent("ready"), atomically: true, encoding: .utf8)
    }
}
let application = NSApplication.shared
let delegate = Delegate()
application.delegate = delegate
application.setActivationPolicy(.accessory)
application.run()
