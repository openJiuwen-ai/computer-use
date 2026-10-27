import AppKit

@MainActor final class AssistField: NSTextField {
    var refuse = false
    override func setAccessibilityFocused(_ value: Bool) {
        guard value, !refuse, NSApp.isActive, window?.isKeyWindow == true else { return }
        super.setAccessibilityFocused(value)
    }
}
@MainActor final class AssistWindow: NSWindow {
    override var canBecomeKey: Bool { true }
    override func setAccessibilityMain(_ value: Bool) { if value { makeMain() } }
    override func setAccessibilityFocused(_ value: Bool) { /* Bridge accepts but cannot make a key window. */  }
}
@MainActor final class AssistApplication: NSApplication {
    override func sendEvent(_ event: NSEvent) {
        super.sendEvent(event)
        if [.keyDown, .keyUp, .leftMouseDown, .leftMouseUp].contains(event.type) {
            (delegate as? AssistDelegate)?.record("event-\(event.type.rawValue)")
        }
    }
}
@MainActor final class AssistDelegate: NSObject, NSApplicationDelegate, NSTextFieldDelegate, NSWindowDelegate {
    var windows = [NSWindow]()
    let source = NSTextField(string: "untouched")
    let target = AssistField(string: "before")
    let completion = AssistField(string: "")
    let refused = AssistField(string: "refused unchanged")
    // Search inputs intentionally keep their keyboard/event route. Cancellation
    // must exercise real key delivery, not a native whole-selection edit.
    let keyboard = NSSearchField(string: "")
    var sheet: NSWindow?
    var transient: AssistField?
    var submitted = ""
    var root: URL { Bundle.main.bundleURL.deletingLastPathComponent() }
    func record(_ event: String) {
        let record: [String: Any] = [
            "event": event, "source": source.stringValue, "target": target.stringValue, "refused": refused.stringValue,
            "completion": completion.stringValue, "active": NSApp.isActive, "key": NSApp.keyWindow?.title ?? "",
            "transient": transient?.stringValue as Any? ?? NSNull(), "submitted": submitted,
            "keyboard": keyboard.stringValue
        ]
        let data = try! JSONSerialization.data(withJSONObject: record, options: [.sortedKeys]) + Data([10])
        let file = root.appendingPathComponent("events.jsonl")
        if !FileManager.default.fileExists(atPath: file.path) {
            FileManager.default.createFile(atPath: file.path, contents: nil)
        }
        let handle = try! FileHandle(forWritingTo: file)
        try! handle.seekToEnd()
        try! handle.write(contentsOf: data)
        try! handle.close()
    }
    func controlTextDidChange(_ notification: Notification) {
        if notification.object as? NSTextField === completion {
            windows[1].makeFirstResponder(target)
            record("completed-and-moved-focus")
        }
    }
    @objc func activateSource() {
        NSApp.activate()
        windows[0].makeKeyAndOrderFront(nil)
    }
    @objc func activateTarget() {
        NSApp.activate()
        windows[1].makeKeyAndOrderFront(nil)
    }
    @objc func continueInWindow() { record("continue-in-window") }
    @objc func openTransient() {
        guard transient == nil else { return }
        let field = AssistField(string: "")
        field.frame = NSRect(x: 20, y: 200, width: 370, height: 28)
        field.setAccessibilityIdentifier("transient")
        field.target = self
        field.action = #selector(submitTransient)
        windows[1].contentView?.addSubview(field)
        transient = field
        windows[1].makeFirstResponder(field)
        record("transient-opened")
    }
    @objc func submitTransient() {
        submitted = transient?.stringValue ?? ""
        transient?.removeFromSuperview()
        transient = nil
        windows[1].makeFirstResponder(target)
        record("transient-submitted")
    }
    func windowDidResignKey(_ notification: Notification) {
        guard notification.object as? NSWindow === windows[1], transient != nil else { return }
        transient?.removeFromSuperview()
        transient = nil
        record("transient-dismissed-on-blur")
    }
    @objc func openSheet() {
        guard sheet == nil else { return }
        let panel = NSWindow(
            contentRect: NSRect(x: 0, y: 0, width: 260, height: 100), styleMask: [.titled], backing: .buffered,
            defer: false)
        panel.title = "Owned modal sheet"
        panel.isReleasedWhenClosed = false
        let close = NSButton(title: "Done", target: self, action: #selector(closeSheet))
        close.frame = NSRect(x: 80, y: 30, width: 100, height: 30)
        close.keyEquivalent = "\r"
        panel.contentView?.addSubview(close)
        sheet = panel
        windows[1].beginSheet(panel)
        record("sheet-opened")
    }
    @objc func closeSheet() {
        guard let panel = sheet else { return }
        windows[1].endSheet(panel)
        panel.orderOut(nil)
        sheet = nil
        record("sheet-closed")
    }
    func applicationDidFinishLaunching(_ notification: Notification) {
        let menu = NSMenu(), edit = NSMenu(title: "Edit")
        edit.addItem(withTitle: "Paste", action: #selector(NSText.paste(_:)), keyEquivalent: "v")
        edit.addItem(withTitle: "Select All", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")
        let open = edit.addItem(withTitle: "Open Sheet", action: #selector(openSheet), keyEquivalent: "o")
        open.target = self
        let transient = edit.addItem(
            withTitle: "Open transient input", action: #selector(openTransient), keyEquivalent: "p")
        transient.target = self
        let item = NSMenuItem(title: "Edit", action: nil, keyEquivalent: "")
        item.submenu = edit
        menu.addItem(item)
        NSApp.mainMenu = menu
        try! String(getpid()).write(to: root.appendingPathComponent("fixture.pid"), atomically: true, encoding: .utf8)
        for (index, title) in ["Source document", "Target document"].enumerated() {
            let window = AssistWindow(
                contentRect: NSRect(x: 120 + index * 450, y: 260, width: 420, height: 280),
                styleMask: [.titled, .closable], backing: .buffered, defer: false)
            window.title = title
            window.isReleasedWhenClosed = false
            window.tabbingMode = .disallowed
            window.delegate = self
            window.orderBack(nil)
            windows.append(window)
        }
        source.frame = NSRect(x: 20, y: 160, width: 370, height: 28)
        source.setAccessibilityIdentifier("source")
        windows[0].contentView?.addSubview(source)
        target.frame = NSRect(x: 20, y: 160, width: 370, height: 28)
        target.setAccessibilityIdentifier("target")
        windows[1].contentView?.addSubview(target)
        keyboard.frame = NSRect(x: 20, y: 240, width: 370, height: 28)
        keyboard.setAccessibilityIdentifier("keyboard")
        windows[1].contentView?.addSubview(keyboard)
        // No pointer geometry: this tests that refusal after activation does not
        // leak focus or fall through to another input field.
        target.setAccessibilityFrame(.zero)
        refused.frame = NSRect(x: 20, y: 100, width: 370, height: 28)
        refused.setAccessibilityIdentifier("refused")
        refused.refuse = true
        windows[1].contentView?.addSubview(refused)
        refused.setAccessibilityFrame(.zero)
        completion.frame = NSRect(x: 20, y: 40, width: 370, height: 28)
        completion.setAccessibilityIdentifier("completion")
        completion.delegate = self
        windows[1].contentView?.addSubview(completion)
        completion.setAccessibilityFrame(.zero)
        let continueButton = NSButton(title: "Continue in window", target: self, action: #selector(continueInWindow))
        continueButton.frame = NSRect(x: 20, y: 4, width: 190, height: 28)
        continueButton.setAccessibilityIdentifier("continue")
        windows[1].contentView?.addSubview(continueButton)
        let disabledButton = NSButton(title: "Disabled action", target: self, action: #selector(continueInWindow))
        disabledButton.frame = NSRect(x: 220, y: 4, width: 160, height: 28)
        disabledButton.isEnabled = false
        disabledButton.setAccessibilityIdentifier("disabled")
        windows[1].contentView?.addSubview(disabledButton)
        windows[0].makeKey()
        windows[0].makeFirstResponder(source)
        Timer.scheduledTimer(withTimeInterval: 0.05, repeats: true) { _ in
            MainActor.assumeIsolated {
                // Test setup only: choose the starting foreground before an SDK
                // operation. This never reads or redirects injected input.
                let setup = self.root.appendingPathComponent("setup-foreground.txt")
                if let value = try? String(contentsOf: setup, encoding: .utf8) {
                    try? FileManager.default.removeItem(at: setup)
                    if value == "source" { self.activateSource() }
                    if value == "target" { self.activateTarget() }
                }
                self.record("sample")
            }
        }
    }
}
let app = AssistApplication.shared
app.setActivationPolicy(.regular)
let delegate = AssistDelegate()
app.delegate = delegate
app.run()
