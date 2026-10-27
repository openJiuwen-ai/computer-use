import AppKit

// Providers may use AXMenu for an always-visible list embedded in a document.
// A role plus visible children does not imply native menu tracking.
@MainActor final class InlineChoices: NSView {
    let item = NSAccessibilityElement()
    override init(frame frameRect: NSRect) {
        super.init(frame: frameRect)
        setAccessibilityElement(true)
        setAccessibilityRole(.menu)
        setAccessibilityLabel("Inline choices")
        item.setAccessibilityRole(.menuItem)
        item.setAccessibilityLabel("Default style")
        item.setAccessibilityParent(self)
    }
    required init?(coder: NSCoder) { fatalError("init(coder:)") }
    override func accessibilityChildren() -> [Any]? { [item] }
    override func accessibilityVisibleChildren() -> [Any]? { [item] }
    override func draw(_ dirtyRect: NSRect) { ("Default style" as NSString).draw(at: NSPoint(x: 5, y: 5)) }
}

@MainActor final class Delegate: NSObject, NSApplicationDelegate, NSMenuDelegate {
    var window: NSWindow!
    var other: NSWindow?
    var otherCancellations = 0
    var opened = false
    var cancellations = 0
    var choice = "Alpha"
    var highlighted = ""
    var popup: NSPopUpButton!
    let directory = Bundle.main.bundleURL.deletingLastPathComponent()
    func record(_ kind: String) {
        let value: [String: Any] = [
            "kind": kind, "open": opened, "cancellations": cancellations, "otherCancellations": otherCancellations,
            "choice": choice, "highlighted": highlighted, "active": NSApp.isActive,
            "foreground": NSWorkspace.shared.frontmostApplication?.processIdentifier ?? 0,
            "time": Date().timeIntervalSince1970
        ]
        let file = directory.appendingPathComponent("events.jsonl")
        if !FileManager.default.fileExists(atPath: file.path) {
            FileManager.default.createFile(atPath: file.path, contents: nil)
        }
        let h = try! FileHandle(forWritingTo: file)
        try! h.seekToEnd()
        try! h.write(contentsOf: JSONSerialization.data(withJSONObject: value) + Data([10]))
        try! h.close()
    }
    func applicationDidFinishLaunching(_ notification: Notification) {
        try! String(getpid()).write(
            to: directory.appendingPathComponent("fixture.pid"), atomically: true, encoding: .utf8)
        window = NSWindow(
            contentRect: NSRect(x: 160, y: 190, width: 360, height: 180), styleMask: [.titled, .closable],
            backing: .buffered, defer: false)
        window.title = "Popup keyboard probe"
        window.isReleasedWhenClosed = false
        popup = NSPopUpButton(frame: NSRect(x: 20, y: 100, width: 220, height: 30))
        popup.addItems(withTitles: ["Alpha", "Beta", "Gamma"])
        popup.setAccessibilityIdentifier("choices")
        popup.menu?.delegate = self
        popup.target = self
        popup.action = #selector(chosen(_:))
        window.contentView!.addSubview(popup)
        if FileManager.default.fileExists(atPath: directory.appendingPathComponent("inline-menu").path) {
            window.contentView!.addSubview(InlineChoices(frame: NSRect(x: 20, y: 65, width: 220, height: 28)))
            let move = NSButton(title: "Focus other", target: self, action: #selector(focusOther))
            move.frame = NSRect(x: 245, y: 65, width: 110, height: 28)
            window.contentView!.addSubview(move)
        }
        let cancel = NSButton(title: "Cancel document", target: self, action: #selector(cancel))
        cancel.frame = NSRect(x: 20, y: 30, width: 160, height: 30)
        cancel.keyEquivalent = "\u{1B}"
        window.contentView!.addSubview(cancel)
        window.orderBack(nil)
        window.makeKey()
        window.makeMain()
        record("ready")
        Timer.scheduledTimer(withTimeInterval: 90, repeats: false) { _ in
            MainActor.assumeIsolated { NSApp.terminate(nil) }
        }
    }
    @objc func focusOther() {
        let next = NSWindow(
            contentRect: NSRect(x: 540, y: 190, width: 240, height: 150), styleMask: [.titled, .closable],
            backing: .buffered, defer: false)
        next.title = "Other document"
        next.isReleasedWhenClosed = false
        let cancel = NSButton(title: "Other cancel", target: self, action: #selector(cancelOther))
        cancel.frame = NSRect(x: 20, y: 50, width: 160, height: 30)
        cancel.keyEquivalent = "\u{1B}"
        next.contentView!.addSubview(cancel)
        other = next
        next.orderBack(nil)
        next.makeKey()
        next.makeMain()
        record("focus-other")
    }
    @objc func cancelOther() {
        otherCancellations += 1
        record("wrong-document")
    }
    func menuWillOpen(_ menu: NSMenu) {
        opened = true
        record("menu-open")
    }
    func menuDidClose(_ menu: NSMenu) {
        opened = false
        record("menu-close")
    }
    func menu(_ menu: NSMenu, willHighlight item: NSMenuItem?) {
        highlighted = item?.title ?? ""
        record("highlight")
    }
    func applicationDidBecomeActive(_ notification: Notification) {
        record("active")
        if opened, FileManager.default.fileExists(atPath: directory.appendingPathComponent("close-on-activation").path)
        {
            popup.menu?.cancelTracking()
        }
    }
    func applicationDidResignActive(_ notification: Notification) { record("inactive") }
    @objc func cancel() {
        cancellations += 1
        record("cancel-document")
    }
    @objc func chosen(_ sender: NSPopUpButton) {
        choice = sender.titleOfSelectedItem ?? ""
        record("choice")
    }
}
let app = NSApplication.shared
app.setActivationPolicy(.regular)
let delegate = Delegate()
app.delegate = delegate
app.run()
