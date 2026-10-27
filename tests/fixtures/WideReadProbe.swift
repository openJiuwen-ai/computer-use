import AppKit

/// A long collection of compound rows. Each anonymous row has its own label
/// and action, as in a file browser; the provider also has a short sibling.
@MainActor final class WideGroup: NSView {
    var childReadDelay: TimeInterval = 0
    override func accessibilityChildren() -> [Any]? {
        if childReadDelay > 0 { Thread.sleep(forTimeInterval: childReadDelay) }
        return subviews
    }
}

@MainActor final class Delegate: NSObject, NSApplicationDelegate {
    var window: NSWindow!
    var presses: [String: Int] = [:]
    let folder = URL(fileURLWithPath: Bundle.main.bundlePath).deletingLastPathComponent()

    func applicationDidFinishLaunching(_ notification: Notification) {
        window = NSWindow(
            contentRect: NSRect(x: 160, y: 200, width: 520, height: 330), styleMask: [.titled, .closable],
            backing: .buffered, defer: false)
        window.title = "Wide read acceptance"
        let scroll = NSScrollView(frame: NSRect(x: 20, y: 65, width: 480, height: 245))
        scroll.hasVerticalScroller = true
        let count = Bundle.main.object(forInfoDictionaryKey: "TestRowCount") as? Int ?? 600
        precondition((1...2000).contains(count))
        let list = WideGroup(frame: NSRect(x: 0, y: 0, width: 460, height: count * 28))
        list.setAccessibilityElement(true)
        list.setAccessibilityRole(.list)
        list.setAccessibilityLabel("Long collection")
        list.setAccessibilityIdentifier("wide-collection")
        for index in 0..<count {
            let row = WideGroup(frame: NSRect(x: 0, y: (count - 1 - index) * 28, width: 440, height: 28))
            row.setAccessibilityElement(true)
            row.setAccessibilityRole(.group)
            row.setAccessibilityIdentifier("row-\(index)")
            row.childReadDelay = Bundle.main.object(forInfoDictionaryKey: "TestChildReadDelay") as? Double ?? 0
            let label = NSTextField(labelWithString: "Item \(index)")
            label.frame = NSRect(x: 8, y: 3, width: 160, height: 22)
            label.setAccessibilityIdentifier("label-\(index)")
            row.addSubview(label)
            let button = NSButton(title: "Choose \(index)", target: self, action: #selector(press(_:)))
            button.frame = NSRect(x: 200, y: 1, width: 160, height: 25)
            button.identifier = NSUserInterfaceItemIdentifier("button-\(index)")
            button.setAccessibilityIdentifier("button-\(index)")
            row.addSubview(button)
            list.addSubview(row)
        }
        scroll.documentView = list
        window.contentView!.addSubview(scroll)
        let finish = NSButton(title: "Finish outside collection", target: self, action: #selector(press(_:)))
        finish.frame = NSRect(x: 20, y: 15, width: 250, height: 32)
        finish.identifier = NSUserInterfaceItemIdentifier("finish")
        finish.setAccessibilityIdentifier("finish")
        window.contentView!.addSubview(finish)
        list.scroll(NSPoint(x: 0, y: list.bounds.height - scroll.contentSize.height))
        window.orderBack(nil)
        save()
        try! String(ProcessInfo.processInfo.processIdentifier).write(
            to: folder.appendingPathComponent("ready"), atomically: true, encoding: .utf8)
        Timer.scheduledTimer(withTimeInterval: 180, repeats: false) { _ in
            MainActor.assumeIsolated { NSApp.terminate(nil) }
        }
    }

    @objc func press(_ sender: NSButton) {
        presses[sender.identifier!.rawValue, default: 0] += 1
        save()
    }

    func save() {
        let state: [String: Any] = [
            "presses": presses, "foreground": Int(NSWorkspace.shared.frontmostApplication?.processIdentifier ?? 0)
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
