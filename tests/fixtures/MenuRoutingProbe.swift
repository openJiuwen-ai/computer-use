import AppKit

@MainActor final class RoutingApplication: NSApplication {
    override func accessibilityMainWindow() -> Any? {
        guard let probe = delegate as? RoutingProbe, !probe.hideReports else { return nil }
        return probe.reportedMain
    }
    override func accessibilityFocusedWindow() -> Any? {
        guard let probe = delegate as? RoutingProbe, !probe.hideReports else { return nil }
        return probe.reportedFocused
    }
    override var accessibilityFocusedUIElement: Any? {
        MainActor.assumeIsolated { () -> NSButton? in
            guard let probe = delegate as? RoutingProbe, !probe.hideResponder else { return nil }
            return probe.buttons[probe.receiver.title]
        }
    }
}
@MainActor final class RoutingWindow: NSWindow {
    weak var probe: RoutingProbe?
    override func setAccessibilityMain(_ value: Bool) {
        if value {
            makeMain()
            probe?.reportedMain = self
        }
        probe?.record("main")
    }
    override func setAccessibilityFocused(_ value: Bool) {
        if value, probe?.focusAvailable == true {
            makeKey()
            makeMain()
            probe?.receiver = self
            probe?.reportedMain = self
            probe?.reportedFocused = self
        }
        probe?.record("focus")
    }
    override func isAccessibilitySelectorAllowed(_ selector: Selector) -> Bool {
        if selector == #selector(setAccessibilityFocused(_:)) { return probe?.focusAvailable == true }
        return super.isAccessibilitySelectorAllowed(selector)
    }
}
@MainActor final class RoutingProbe: NSObject, NSApplicationDelegate {
    var first: RoutingWindow!, second: RoutingWindow!
    var receiver: RoutingWindow!, reportedMain: RoutingWindow!, reportedFocused: RoutingWindow!
    var hideReports = true, focusAvailable = false, hideResponder = false
    var counts = ["Alpha": 0, "Beta": 0]
    var labels = [String: NSTextField]()
    var buttons = [String: NSButton]()
    let directory = Bundle.main.bundleURL.deletingLastPathComponent()
    func record(_ kind: String) {
        for (name, label) in labels { label.stringValue = "\(name) commands: \(counts[name]!)" }
        let data = try! JSONSerialization.data(withJSONObject: [
            "kind": kind, "counts": counts, "receiver": receiver?.title ?? "none",
            "main": reportedMain?.title ?? "none", "hideReports": hideReports, "focusAvailable": focusAvailable
        ])
        try! data.write(to: directory.appendingPathComponent("state.json"), options: .atomic)
        let file = directory.appendingPathComponent("events.jsonl")
        if !FileManager.default.fileExists(atPath: file.path) {
            FileManager.default.createFile(atPath: file.path, contents: nil)
        }
        let h = try! FileHandle(forWritingTo: file)
        try! h.seekToEnd()
        try! h.write(contentsOf: data + Data([10]))
        try! h.close()
    }
    func window(_ name: String, _ x: Double) -> RoutingWindow {
        let window = RoutingWindow(
            contentRect: NSRect(x: x, y: 200, width: 400, height: 320), styleMask: [.titled, .closable],
            backing: .buffered, defer: false)
        window.title = name
        window.probe = self
        window.isReleasedWhenClosed = false
        let label = NSTextField(labelWithString: "")
        label.frame = NSRect(x: 20, y: 160, width: 360, height: 30)
        window.contentView?.addSubview(label)
        labels[name] = label
        let reveal = NSButton(title: "Reveal window reports", target: self, action: #selector(revealReports))
        reveal.frame = NSRect(x: 20, y: 125, width: 300, height: 30)
        window.contentView?.addSubview(reveal)
        let button = NSButton(title: "Enable native window focus", target: self, action: #selector(enableFocus))
        button.frame = NSRect(x: 20, y: 85, width: 300, height: 30)
        window.contentView?.addSubview(button)
        let direct = NSButton(title: "Record this window", target: self, action: #selector(recordDirect(_:)))
        direct.frame = NSRect(x: 20, y: 35, width: 300, height: 30)
        window.contentView?.addSubview(direct)
        buttons[name] = direct
        let misleading = NSButton(
            title: "Report target without responder", target: self, action: #selector(reportWithoutResponder))
        misleading.frame = NSRect(x: 20, y: 240, width: 350, height: 30)
        window.contentView?.addSubview(misleading)
        let responder = NSButton(title: "Reveal responder", target: self, action: #selector(revealResponder))
        responder.frame = NSRect(x: 20, y: 200, width: 300, height: 30)
        window.contentView?.addSubview(responder)
        window.orderBack(nil)
        return window
    }
    func applicationDidFinishLaunching(_ notification: Notification) {
        try! String(getpid()).write(
            to: directory.appendingPathComponent("fixture.pid"), atomically: true, encoding: .utf8)
        let menu = NSMenu(), heading = NSMenuItem(title: "Document", action: nil, keyEquivalent: "")
        let appMenu = NSMenuItem(title: "Probe", action: nil, keyEquivalent: "")
        appMenu.submenu = NSMenu(title: "Probe")
        menu.addItem(appMenu)
        heading.submenu = NSMenu(title: "Document")
        heading.submenu!.autoenablesItems = false
        let item = heading.submenu!.addItem(
            withTitle: "Record document", action: #selector(recordMenu), keyEquivalent: "")
        item.target = self
        menu.addItem(heading)
        NSApp.mainMenu = menu
        first = window("Alpha", 100)
        second = window("Beta", 550)
        receiver = first
        reportedMain = first
        reportedFocused = first
        first.makeKey()
        first.makeMain()
        record("ready")
    }
    @objc func recordMenu() {
        counts[receiver.title, default: 0] += 1
        record("menu")
    }
    @objc func recordDirect(_ button: NSButton) {
        counts[button.window!.title, default: 0] += 1
        record("direct")
    }
    @objc func enableFocus() {
        hideReports = false
        focusAvailable = true
        record("enable")
    }
    @objc func revealReports() {
        hideReports = false
        record("reveal")
    }
    @objc func reportWithoutResponder() {
        hideReports = false
        hideResponder = true
        reportedMain = second
        reportedFocused = second
        record("misleading")
    }
    @objc func revealResponder() {
        hideResponder = false
        record("responder")
    }
}
let application = RoutingApplication.shared
application.setActivationPolicy(.regular)
let delegate = RoutingProbe()
application.delegate = delegate
application.run()
