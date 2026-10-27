import AppKit
import ApplicationServices

/// A real keyboard editor exposed directly in AXWindows. Like a transient
/// field editor, it has no AXWindow/AXTopLevelUIElement or focused-window report.
/// Only the AX representation is customized; AppKit still routes every key.
@MainActor final class RootEditor: NSTextView {
    weak var probe: RootEditorDelegate?
    override func accessibilityParent() -> Any? { NSApp }
    override func accessibilityWindow() -> Any? { nil }
    override func accessibilityTopLevelUIElement() -> Any? { nil }
    override func isAccessibilityFocused() -> Bool { window?.firstResponder === self }
    override func insertNewline(_ sender: Any?) { probe?.commit() }
}

@MainActor final class RootEditorApplication: NSApplication {
    var probe: RootEditorDelegate? { delegate as? RootEditorDelegate }
    override func accessibilityWindows() -> [Any]? {
        guard let probe else { return [] }
        return [probe.editor, probe.other]
    }
    override func accessibilityFocusedWindow() -> Any? {
        guard let probe else { return nil }
        return probe.mode == "conflict" || keyWindow === probe.other ? probe.other : nil
    }
    override func accessibilityMainWindow() -> Any? { nil }
    override func accessibilityApplicationFocusedUIElement() -> Any? {
        guard let probe else { return nil }
        if probe.mode == "missing" { return nil }
        return keyWindow === probe.other ? probe.guardField : probe.editor
    }
}

@MainActor final class RootEditorDelegate: NSObject, NSApplicationDelegate, NSTextViewDelegate {
    let directory = Bundle.main.bundleURL.deletingLastPathComponent()
    let input = NSWindow(
        contentRect: NSRect(x: 220, y: 300, width: 420, height: 90), styleMask: [.titled, .closable],
        backing: .buffered, defer: false)
    let other = NSWindow(
        contentRect: NSRect(x: 680, y: 300, width: 400, height: 140), styleMask: [.titled, .closable],
        backing: .buffered, defer: false)
    let editor = RootEditor()
    let guardField = NSTextField(string: "GUARD")
    var mode = "normal", armed = false, commits = 0

    func record() {
        let state: [String: Any] = [
            "text": editor.string, "guard": guardField.stringValue, "commits": commits, "mode": mode, "armed": armed,
            "inputWindow": input.windowNumber, "otherWindow": other.windowNumber,
            "keyWindow": NSApp.keyWindow?.windowNumber ?? -1, "actualEditor": input.firstResponder === editor,
            "foreground": NSWorkspace.shared.frontmostApplication?.processIdentifier == getpid()
        ]
        try! JSONSerialization.data(withJSONObject: state, options: [.sortedKeys]).write(
            to: directory.appendingPathComponent("state.json"), options: .atomic)
    }
    func applicationDidFinishLaunching(_ notification: Notification) {
        input.title = "Root editor"
        other.title = "Unrelated document"
        for window in [input, other] {
            window.tabbingMode = .disallowed
            window.isReleasedWhenClosed = false
        }
        editor.frame = NSRect(x: 20, y: 30, width: 380, height: 28)
        editor.setAccessibilityElement(true)
        editor.string = "before"
        editor.isRichText = false
        editor.probe = self
        editor.setAccessibilityIdentifier("root-editor")
        editor.delegate = self
        input.contentView!.addSubview(editor)
        guardField.frame = NSRect(x: 20, y: 70, width: 350, height: 28)
        guardField.setAccessibilityIdentifier("guard")
        other.contentView!.addSubview(guardField)
        let menu = NSMenu(), edit = NSMenuItem(title: "Edit", action: nil, keyEquivalent: "")
        edit.submenu = NSMenu(title: "Edit")
        edit.submenu!.addItem(withTitle: "Paste", action: #selector(NSText.paste(_:)), keyEquivalent: "v")
        menu.addItem(edit)
        NSApp.mainMenu = menu
        other.orderBack(nil)
        input.orderBack(nil)
        input.makeKey()
        input.makeFirstResponder(editor)
        try! String(getpid()).write(
            to: directory.appendingPathComponent("fixture.pid"), atomically: true, encoding: .utf8)
        record()
        // Independent host setup, never an input route available to the Agent.
        Timer.scheduledTimer(withTimeInterval: 0.05, repeats: true) { _ in
            MainActor.assumeIsolated {
                let command = self.directory.appendingPathComponent("command.txt")
                if let value = try? String(contentsOf: command, encoding: .utf8) {
                    try? FileManager.default.removeItem(at: command)
                    if value == "arm" {
                        self.armed = true
                    } else {
                        self.mode = value
                        self.input.makeKey()
                        self.input.makeFirstResponder(self.editor)
                    }
                }
                self.record()
            }
        }
    }
    @objc func commit() {
        commits += 1
        record()
    }
    func textDidChange(_ notification: Notification) {
        if armed && editor.string.contains("#") {
            armed = false
            input.makeFirstResponder(nil)
            other.makeKey()
            other.makeFirstResponder(guardField)
        }
        record()
    }
}

if CommandLine.arguments.count == 3, CommandLine.arguments[1] == "--inspect" {
    let application = AXUIElementCreateApplication(pid_t(CommandLine.arguments[2])!)
    func read(_ element: AXUIElement, _ name: String) -> (AXError, CFTypeRef?) {
        var value: CFTypeRef?
        return (AXUIElementCopyAttributeValue(element, name as CFString, &value), value)
    }
    let (status, raw) = read(application, kAXFocusedUIElementAttribute)
    var result: [String: Any] = [
        "focusStatus": status.rawValue, "focusedWindowStatus": read(application, kAXFocusedWindowAttribute).0.rawValue
    ]
    if let raw, CFGetTypeID(raw) == AXUIElementGetTypeID() {
        let focus = unsafeDowncast(raw, to: AXUIElement.self)
        result["role"] = read(focus, kAXRoleAttribute).1 as? String
        result["focused"] = read(focus, kAXFocusedAttribute).1 as? Bool
        result["windowStatus"] = read(focus, kAXWindowAttribute).0.rawValue
        result["topLevelStatus"] = read(focus, kAXTopLevelUIElementAttribute).0.rawValue
        result["declaredRoot"] =
            (read(application, kAXWindowsAttribute).1 as? [AXUIElement])?.contains { CFEqual($0, focus) } ?? false
    }
    print(String(data: try! JSONSerialization.data(withJSONObject: result, options: [.sortedKeys]), encoding: .utf8)!)
    exit(0)
}

let app = RootEditorApplication.shared
app.setActivationPolicy(.regular)
let delegate = RootEditorDelegate()
app.delegate = delegate
app.run()
