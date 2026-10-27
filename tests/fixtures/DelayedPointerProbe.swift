import AppKit

// Activation/key focus can settle before window ordering. Deliberately separate
// those transitions while retaining the same document and native window.
@MainActor final class DelayedWindow: NSWindow {
    var delay: TimeInterval = 0.3
    var raises = 0
    var onRaise: (() -> Void)?
    var immediateRaise = false
    override func accessibilityPerformRaise() -> Bool {
        raises += 1
        onRaise?()
        if immediateRaise { return super.accessibilityPerformRaise() }
        orderBack(nil)
        DispatchQueue.main.asyncAfter(deadline: .now() + delay) { [weak self] in self?.orderFront(nil) }
        return true
    }
}
@MainActor final class DelayedDelegate: NSObject, NSApplicationDelegate, NSTextFieldDelegate {
    let directory = Bundle.main.bundleURL.deletingLastPathComponent()
    var window: DelayedWindow!
    var parentWindow: NSWindow?
    var field: NSTextField!
    var submissions = 0
    func record() {
        let state: [String: Any] = ["value": field.stringValue, "submissions": submissions, "raises": window.raises]
        try! JSONSerialization.data(withJSONObject: state).write(
            to: directory.appendingPathComponent("state.json"), options: .atomic)
    }
    func applicationDidFinishLaunching(_ notification: Notification) {
        try! String(getpid()).write(
            to: directory.appendingPathComponent("fixture.pid"), atomically: true, encoding: .utf8)
        window = DelayedWindow(
            contentRect: NSRect(x: 140, y: 160, width: 480, height: 220), styleMask: [.titled, .closable],
            backing: .buffered, defer: false)
        window.title = "Delayed Pointer Probe"
        window.isReleasedWhenClosed = false
        if FileManager.default.fileExists(atPath: directory.appendingPathComponent("never-ready").path) {
            window.delay = 30
        }
        let sheet = FileManager.default.fileExists(atPath: directory.appendingPathComponent("attached-sheet").path)
        window.immediateRaise = sheet
        field = NSTextField(frame: NSRect(x: 25, y: 95, width: 410, height: 24))
        field.stringValue = "guard"
        field.setAccessibilityIdentifier("delayed-field")
        field.delegate = self
        field.target = self
        field.action = #selector(submit)
        window.contentView!.addSubview(field)
        window.onRaise = { [weak self] in self?.record() }
        // AppKit dispatches standard editing shortcuts through the menu chain.
        let menu = NSMenu(), edit = NSMenu(title: "Edit"),
            item = NSMenuItem(title: "Edit", action: nil, keyEquivalent: "")
        edit.addItem(withTitle: "Select All", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")
        item.submenu = edit
        menu.addItem(item)
        NSApp.mainMenu = menu
        if sheet {
            parentWindow = NSWindow(
                contentRect: NSRect(x: 140, y: 160, width: 560, height: 350), styleMask: [.titled, .closable],
                backing: .buffered, defer: false)
            parentWindow!.title = "Sheet Parent"
            parentWindow!.isReleasedWhenClosed = false
            parentWindow!.orderBack(nil)
            parentWindow!.makeKey()
            parentWindow!.makeMain()
            parentWindow!.beginSheet(window)
        } else {
            window.orderBack(nil)
            window.makeKey()
            window.makeMain()
        }
        record()
        Timer.scheduledTimer(withTimeInterval: 90, repeats: false) { _ in
            MainActor.assumeIsolated { NSApp.terminate(nil) }
        }
    }
    @objc func submit() {
        submissions += 1
        record()
    }
    func controlTextDidChange(_ notification: Notification) { record() }
}
let app = NSApplication.shared
app.setActivationPolicy(.regular)
let delegate = DelayedDelegate()
app.delegate = delegate
app.run()
