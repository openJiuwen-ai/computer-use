import AppKit

/// A real two-window editor with all public application AX entry points absent.
/// Its own event log is acceptance evidence, never an input route for the Agent.
@MainActor final class VisualApplication: NSApplication {
    override func accessibilityRole() -> NSAccessibility.Role? { nil }
    override func accessibilityChildren() -> [Any]? { [] }
    override func accessibilityWindows() -> [Any]? {
        if ProcessInfo.processInfo.environment["COMPUTER_USE_TEST_LATE_WINDOWS"] == "1" {
            (delegate as? VisualDelegate)?.createWindows(visible: false)
        }
        return []
    }
    override func accessibilityFocusedWindow() -> Any? { nil }
    override func accessibilityMainWindow() -> Any? { nil }
    override func accessibilityApplicationFocusedUIElement() -> Any? { nil }
    override func sendEvent(_ event: NSEvent) {
        // Record key arrival before AppKit handles it so a host can cancel
        // while modifiers are still held, without delaying or filtering input.
        if [.keyDown, .keyUp, .flagsChanged].contains(event.type) {
            (delegate as? VisualDelegate)?.record("event-received", event: event)
        }
        super.sendEvent(event)
        if [.keyDown, .keyUp, .flagsChanged, .leftMouseDown, .leftMouseUp].contains(event.type) {
            (delegate as? VisualDelegate)?.record("event", event: event)
        }
    }
}

@MainActor final class VisualWindow: NSWindow {
    override var canBecomeKey: Bool { true }
    override func accessibilityRole() -> NSAccessibility.Role? { nil }
    override func accessibilityChildren() -> [Any]? { [] }
}

@MainActor final class VisualDelegate: NSObject, NSApplicationDelegate {
    let directory = Bundle.main.bundleURL.deletingLastPathComponent()
    var windows = [String: NSWindow]()
    var fields = [String: NSTextField]()
    var log: FileHandle!
    func record(_ kind: String, event: NSEvent? = nil, sender: String? = nil) {
        var value: [String: Any] = [
            "kind": kind, "values": fields.mapValues(\.stringValue), "keyWindow": NSApp.keyWindow?.title ?? "none",
            "mainWindow": NSApp.mainWindow?.title ?? "none", "windows": windows.mapValues(\.windowNumber),
            "selections": fields.compactMapValues { field -> [Int]? in
                guard let editor = field.currentEditor() as? NSTextView else { return nil }
                let range = editor.selectedRange()
                return [range.location, range.length]
            }
        ]
        if let event {
            value["event"] = event.type.rawValue
            value["window"] = event.windowNumber
            if [.keyDown, .keyUp, .flagsChanged].contains(event.type) { value["keyCode"] = event.keyCode }
            value["modifiers"] = event.modifierFlags.rawValue
        }
        if let sender { value["sender"] = sender }
        try! log.write(contentsOf: JSONSerialization.data(withJSONObject: value) + Data([10]))
    }
    func applicationDidFinishLaunching(_ notification: Notification) {
        NSWindow.allowsAutomaticWindowTabbing = false
        let url = directory.appendingPathComponent("events.jsonl")
        FileManager.default.createFile(atPath: url.path, contents: nil)
        log = try! FileHandle(forWritingTo: url)
        try! Data(String(ProcessInfo.processInfo.processIdentifier).utf8).write(
            to: directory.appendingPathComponent("fixture.pid"))
        // Desktop shortcuts follow NSApplication's normal menu/responder chain.
        // A bare programmatic AppKit app has no Edit > Select All command.
        let menu = NSMenu(), appItem = NSMenuItem(),
            editItem = NSMenuItem(title: "Edit", action: nil, keyEquivalent: "")
        appItem.submenu = NSMenu()
        appItem.submenu!.addItem(withTitle: "Quit", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        editItem.submenu = NSMenu(title: "Edit")
        editItem.submenu!.addItem(withTitle: "Select All", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")
        menu.addItem(appItem)
        menu.addItem(editItem)
        NSApp.mainMenu = menu
        if ProcessInfo.processInfo.environment["COMPUTER_USE_TEST_LATE_WINDOWS"] != "1" { createWindows(visible: true) }
        Timer.scheduledTimer(withTimeInterval: 120, repeats: false) { _ in
            MainActor.assumeIsolated { NSApp.terminate(nil) }
        }
    }
    func createWindows(visible: Bool) {
        guard log != nil, windows.isEmpty else { return }
        for (name, x) in [("Main", 60.0), ("Other", 500.0)] {
            let window = VisualWindow(
                contentRect: NSRect(x: x, y: 260, width: 400, height: 200), styleMask: [.titled, .closable],
                backing: .buffered, defer: false)
            window.title = name
            if visible { window.level = .floating }
            window.isReleasedWhenClosed = false
            let field = NSTextField(string: name)
            field.frame = NSRect(x: 30, y: 100, width: 340, height: 24)
            field.cell?.sendsActionOnEndEditing = false
            field.target = self
            field.action = #selector(submit(_:))
            window.contentView!.addSubview(field)
            let label = NSTextField(labelWithString: "Enter text here, then press Return")
            label.frame = NSRect(x: 30, y: 145, width: 340, height: 24)
            window.contentView!.addSubview(label)
            if name == "Other" {
                let button = NSButton(title: "Move focus to Main", target: self, action: #selector(moveFocus))
                button.frame = NSRect(x: 30, y: 35, width: 260, height: 30)
                window.contentView!.addSubview(button)
            }
            windows[name] = window
            fields[name] = field
            // The visual-input test needs visible fields; the shared read-race
            // test creates windows during AX collection and keeps them behind.
            if visible { window.orderFrontRegardless() } else { window.orderBack(nil) }
        }
        windows["Main"]!.makeKey()
        windows["Main"]!.makeMain()
        windows["Main"]!.makeFirstResponder(fields["Main"])
        record("ready")
    }
    @objc func submit(_ field: NSTextField) { record("submit", sender: field.window?.title) }
    @objc func moveFocus() {
        windows["Main"]!.makeKey()
        windows["Main"]!.makeMain()
        windows["Main"]!.makeFirstResponder(fields["Main"])
        record("focus-moved")
    }
}

let application = VisualApplication.shared
application.setActivationPolicy(.regular)
let delegate = VisualDelegate()
application.delegate = delegate
application.run()
