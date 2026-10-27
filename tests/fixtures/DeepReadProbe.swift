import AppKit

/// A real AppKit button below a long, named layout chain. The view hierarchy,
/// not a business application's name or a mocked driver, supplies AXChildren.
@MainActor final class DeepGroup: NSView { override func accessibilityChildren() -> [Any]? { subviews } }

@MainActor final class Delegate: NSObject, NSApplicationDelegate {
    var window: NSWindow!
    var count = 0
    let folder = URL(fileURLWithPath: Bundle.main.bundlePath).deletingLastPathComponent()
    func applicationDidFinishLaunching(_ notification: Notification) {
        window = NSWindow(
            contentRect: NSRect(x: 150, y: 180, width: 450, height: 160), styleMask: [.titled, .closable],
            backing: .buffered, defer: false)
        window.title = "Deep read acceptance"
        var parent = window.contentView!
        for index in 0..<96 {
            let group = DeepGroup(frame: parent.bounds)
            group.setAccessibilityElement(true)
            group.setAccessibilityRole(.group)
            group.setAccessibilityLabel("Layer \(index)")
            parent.addSubview(group)
            parent = group
        }
        let button = NSButton(title: "Deep action", target: self, action: #selector(press))
        button.frame = NSRect(x: 30, y: 55, width: 160, height: 35)
        button.setAccessibilityIdentifier("deep-action")
        parent.addSubview(button)
        let text = NSTextField(labelWithString: "Deep readable content")
        text.frame = NSRect(x: 210, y: 55, width: 200, height: 35)
        text.setAccessibilityIdentifier("deep-text")
        parent.addSubview(text)
        window.orderBack(nil)
        save()
        try! String(ProcessInfo.processInfo.processIdentifier).write(
            to: folder.appendingPathComponent("ready"), atomically: true, encoding: .utf8)
        Timer.scheduledTimer(withTimeInterval: 180, repeats: false) { _ in
            MainActor.assumeIsolated { NSApp.terminate(nil) }
        }
    }
    @objc func press() {
        count += 1
        save()
    }
    func save() {
        let state = [
            "presses": count, "foreground": Int(NSWorkspace.shared.frontmostApplication?.processIdentifier ?? 0)
        ]
        try! JSONSerialization.data(withJSONObject: state).write(
            to: folder.appendingPathComponent("state.json"), options: .atomic)
    }
}
let application = NSApplication.shared
let delegate = Delegate()
application.delegate = delegate
application.setActivationPolicy(.accessory)
application.run()
