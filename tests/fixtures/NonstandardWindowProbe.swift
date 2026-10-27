import AppKit

// A legitimate AXWindows entry need not advertise AXWindow. Its accessible
// content bounds can also differ from the owning WindowServer surface.
@MainActor final class ContentWindow: NSWindow {
    override func accessibilityRole() -> NSAccessibility.Role? { .textField }
    override func accessibilityFrame() -> NSRect { super.accessibilityFrame().insetBy(dx: 3, dy: 3) }
}

// Content-sized editors resize their window while an AX value is being set.
// The native control/window identity stays the same throughout the edit.
@MainActor final class ResizingField: NSTextField {
    var onEdit: (() -> Void)?
    override func setAccessibilityValue(_ value: Any?) {
        super.setAccessibilityValue(value)
        onEdit?()
    }
}

@MainActor final class SurfaceApplication: NSApplication {
    override func sendEvent(_ event: NSEvent) {
        if [.leftMouseDown, .keyDown].contains(event.type), let probe = delegate as? SurfaceDelegate {
            probe.events += 1
            probe.record()
        }
        super.sendEvent(event)
    }
}

@MainActor final class SurfaceDelegate: NSObject, NSApplicationDelegate, NSTextFieldDelegate {
    let directory = Bundle.main.bundleURL.deletingLastPathComponent()
    var window: ContentWindow!
    let field = ResizingField(string: "original")
    let untouched = NSTextField(string: "unchanged")
    var submissions = 0
    var events = 0
    var textChanges = 0
    func record() {
        try! JSONSerialization.data(withJSONObject: [
            "value": field.stringValue, "untouched": untouched.stringValue, "submissions": submissions,
            "events": events, "width": window.frame.width, "textChanges": textChanges
        ]).write(to: directory.appendingPathComponent("state.json"), options: .atomic)
    }
    func applicationDidFinishLaunching(_ notification: Notification) {
        try! String(getpid()).write(
            to: directory.appendingPathComponent("fixture.pid"), atomically: true, encoding: .utf8)
        window = ContentWindow(
            contentRect: NSRect(x: 180, y: 160, width: 400, height: 160), styleMask: [.titled, .closable],
            backing: .buffered, defer: false)
        window.title = "Nonstandard Surface Probe"
        window.isReleasedWhenClosed = false
        window.representedURL = directory.appendingPathComponent("initial.txt")
        field.frame = NSRect(x: 20, y: 90, width: 360, height: 30)
        field.setAccessibilityIdentifier("surface-editor")
        field.cell?.sendsActionOnEndEditing = false
        field.delegate = self
        field.target = self
        field.action = #selector(submit)
        field.onEdit = { [weak self] in self?.resizeToContent() }
        untouched.frame = NSRect(x: 20, y: 30, width: 360, height: 30)
        untouched.setAccessibilityIdentifier("surface-guard")
        window.contentView!.addSubview(field)
        window.contentView!.addSubview(untouched)
        if FileManager.default.fileExists(atPath: directory.appendingPathComponent("keyboard-menu").path) {
            let menu = NSMenu(), edit = NSMenuItem(title: "Edit", action: nil, keyEquivalent: "")
            edit.submenu = NSMenu()
            edit.submenu!.addItem(withTitle: "Select All", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")
            edit.submenu!.addItem(withTitle: "Paste", action: #selector(NSText.paste(_:)), keyEquivalent: "v")
            menu.addItem(edit)
            NSApp.mainMenu = menu
        }
        window.orderBack(nil)
        window.makeKey()
        window.makeMain()
        window.makeFirstResponder(field)
        record()
        Timer.scheduledTimer(withTimeInterval: 120, repeats: false) { _ in
            MainActor.assumeIsolated { NSApp.terminate(nil) }
        }
    }
    func resizeToContent() {
        textChanges += 1
        if FileManager.default.fileExists(atPath: directory.appendingPathComponent("change-document-on-edit").path) {
            window.representedURL = directory.appendingPathComponent("changed.txt")
        }
        if FileManager.default.fileExists(atPath: directory.appendingPathComponent("resize-on-edit").path) {
            window.setContentSize(NSSize(width: 400 + field.stringValue.count * 10, height: 160))
        }
        record()
    }
    func controlTextDidChange(_ notification: Notification) { resizeToContent() }
    @objc func submit() {
        submissions += 1
        record()
    }
}
let application = SurfaceApplication.shared
application.setActivationPolicy(.regular)
let delegate = SurfaceDelegate()
application.delegate = delegate
application.run()
