import AppKit

@MainActor enum Evidence {
    static let root = Bundle.main.bundleURL.deletingLastPathComponent()
    static func append(_ item: [String: Any]) {
        let url = root.appendingPathComponent("events.jsonl")
        if !FileManager.default.fileExists(atPath: url.path) {
            FileManager.default.createFile(atPath: url.path, contents: nil)
        }
        guard let file = try? FileHandle(forWritingTo: url),
            let data = try? JSONSerialization.data(withJSONObject: item)
        else { return }
        defer { try? file.close() }
        _ = try? file.seekToEnd()
        try? file.write(contentsOf: data + Data([10]))
    }
}
@MainActor final class App: NSApplication {
    override func sendEvent(_ event: NSEvent) {
        if [.leftMouseDown, .rightMouseDown, .rightMouseUp, .keyDown].contains(event.type) {
            Evidence.append([
                "kind": "event", "type": event.type.rawValue, "window": event.windowNumber, "active": isActive,
                "front": NSWorkspace.shared.frontmostApplication?.processIdentifier ?? 0
            ])
        }
        super.sendEvent(event)
    }
}
@MainActor final class Surface: NSView {
    override func draw(_ dirtyRect: NSRect) {
        NSColor.systemGreen.withAlphaComponent(0.25).setFill()
        bounds.fill()
        ("Right click here for a standard NSMenu" as NSString).draw(
            at: NSPoint(x: 20, y: 80), withAttributes: [.font: NSFont.systemFont(ofSize: 17)])
    }
}
@MainActor final class Delegate: NSObject, NSApplicationDelegate, NSMenuDelegate {
    var window: NSWindow!, status: NSTextField!, popup: NSMenu!
    var opens = 0, closes = 0, selections = 0, otherSelections = 0
    func write() {
        let value: [String: Any] = [
            "opens": opens, "closes": closes, "selections": selections, "otherSelections": otherSelections,
            "active": NSApp.isActive, "front": NSWorkspace.shared.frontmostApplication?.processIdentifier ?? 0,
            "key": NSApp.keyWindow?.windowNumber ?? 0
        ]
        if let data = try? JSONSerialization.data(withJSONObject: value, options: .sortedKeys) {
            try? data.write(to: Evidence.root.appendingPathComponent("values.json"))
        }
    }
    func menuWillOpen(_ menu: NSMenu) {
        opens += 1
        Evidence.append(["kind": "willOpen"])
        write()
    }
    func menuDidClose(_ menu: NSMenu) {
        closes += 1
        Evidence.append(["kind": "didClose"])
        write()
    }
    @objc func mark() {
        selections += 1
        status.stringValue = "Selections: \(selections)"
        Evidence.append(["kind": "selected"])
        write()
    }
    @objc func other() {
        otherSelections += 1
        status.stringValue = "Wrong selection"
        write()
    }
    func applicationDidFinishLaunching(_ notification: Notification) {
        try? String(ProcessInfo.processInfo.processIdentifier).write(
            to: Evidence.root.appendingPathComponent("fixture.pid"), atomically: true, encoding: .utf8)
        let main = NSMenu(), item = NSMenuItem(), own = NSMenu()
        own.addItem(withTitle: "Quit Context Probe", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        item.submenu = own
        main.addItem(item)
        NSApp.mainMenu = main
        window = NSWindow(
            contentRect: NSRect(x: 180, y: 200, width: 480, height: 280), styleMask: [.titled, .closable],
            backing: .buffered, defer: false)
        window.title = "Context Menu Probe"
        window.isReleasedWhenClosed = false
        status = NSTextField(labelWithString: "Selections: 0")
        status.frame = NSRect(x: 20, y: 15, width: 420, height: 24)
        window.contentView?.addSubview(status)
        popup = NSMenu(title: "Context Commands")
        popup.delegate = self
        popup.autoenablesItems = false
        let command = popup.addItem(withTitle: "Mark target", action: #selector(Delegate.mark), keyEquivalent: "")
        command.target = self
        popup.addItem(withTitle: "Second target", action: #selector(Delegate.other), keyEquivalent: "").target = self
        let view = Surface(frame: NSRect(x: 20, y: 60, width: 440, height: 200))
        view.setAccessibilityElement(true)
        view.setAccessibilityRole(.group)
        view.setAccessibilityLabel("Context surface")
        view.menu = popup
        window.contentView?.addSubview(view)
        window.orderBack(nil)
        window.makeKey()
        window.makeMain()
        write()
    }
}
let app = App.shared
let delegate = Delegate()
app.delegate = delegate
app.setActivationPolicy(.regular)
app.run()
