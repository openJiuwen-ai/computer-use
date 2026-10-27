import AppKit

// A coarse application focus report, with truthful per-editor focus. This is
// a provider behavior, not an application-name exception in the driver.
@MainActor final class FocusScopeApplication: NSApplication {
    override func accessibilityApplicationFocusedUIElement() -> Any? {
        guard let probe = delegate as? FocusScopeDelegate else { return nil }
        switch probe.mode {
        case "peer": return probe.other
        case "window": return probe.window
        case "orphan", "orphan-handoff": return probe.orphan
        case "missing", "missing-handoff": return nil
        default: return probe.container
        }
    }
}

@MainActor final class FocusContainer: NSView {
    override func isAccessibilityElement() -> Bool { true }
    override func accessibilityRole() -> NSAccessibility.Role? { .group }
    override func isAccessibilityFocused() -> Bool { false }
}

@MainActor final class ScopeEditor: NSTextView {
    override func keyDown(with event: NSEvent) {
        super.keyDown(with: event)
        if let probe = NSApp.delegate as? FocusScopeDelegate {
            probe.keys += 1
            if ["handoff", "missing-handoff"].contains(probe.mode) { window?.makeFirstResponder(probe.other) }
            if probe.mode == "orphan-handoff" { probe.switchWindow() }
            probe.record()
        }
    }
}

@MainActor final class FocusScopeDelegate: NSObject, NSApplicationDelegate, NSTextViewDelegate {
    let directory = URL(fileURLWithPath: CommandLine.arguments[1])
    var window: NSWindow!
    let container = FocusContainer(frame: NSRect(x: 10, y: 10, width: 440, height: 230))
    let target = ScopeEditor(frame: NSRect(x: 15, y: 125, width: 410, height: 80))
    let other = NSTextView(frame: NSRect(x: 15, y: 20, width: 410, height: 80))
    // An unowned AX report models stale/incomplete provider focus metadata.
    // AppKit's actual key window and responder still route real input.
    let orphan = FocusContainer(frame: .zero)
    var otherWindow: NSWindow?
    let otherWindowEditor = NSTextView(frame: NSRect(x: 10, y: 10, width: 300, height: 100))
    var keys = 0
    var lastMode = ""
    var mode: String {
        (try? String(contentsOf: directory.appendingPathComponent("mode"), encoding: .utf8)) ?? "coarse"
    }
    func switchWindow() {
        if otherWindow == nil {
            let sibling = NSWindow(
                contentRect: NSRect(x: 700, y: 150, width: 320, height: 120), styleMask: [.titled, .closable],
                backing: .buffered, defer: false)
            sibling.title = "Independent window"
            sibling.tabbingMode = .disallowed
            otherWindowEditor.isRichText = false
            otherWindowEditor.string = "OTHER-WINDOW"
            sibling.contentView!.addSubview(otherWindowEditor)
            otherWindow = sibling
        }
        otherWindow!.orderBack(nil)
        otherWindow!.makeKey()
        otherWindow!.makeMain()
        otherWindow!.makeFirstResponder(otherWindowEditor)
    }
    func record() {
        // Provider setup: start the missing-report case in the other editor,
        // so addressed input must prepare the requested editor itself.
        if mode != lastMode {
            lastMode = mode
            if mode == "missing" { window.makeFirstResponder(other) }
        }
        let value: [String: Any] = [
            "text": target.string, "other": other.string, "keys": keys,
            "targetResponder": window.firstResponder === target, "mode": mode, "otherWindow": otherWindowEditor.string,
            "keyWindow": NSApp.keyWindow?.title ?? "",
            "selection": [target.selectedRange().location, target.selectedRange().length],
            "foreground": NSWorkspace.shared.frontmostApplication?.processIdentifier ?? -1,
            "pointer": [NSEvent.mouseLocation.x, NSEvent.mouseLocation.y]
        ]
        try? JSONSerialization.data(withJSONObject: value).write(
            to: directory.appendingPathComponent("state.json"), options: .atomic)
    }
    func textDidChange(_ notification: Notification) { record() }
    func applicationDidFinishLaunching(_ notification: Notification) {
        window = NSWindow(
            contentRect: NSRect(x: 160, y: 150, width: 460, height: 260), styleMask: [.titled, .closable],
            backing: .buffered, defer: false)
        window.title = "Coarse native focus"
        window.tabbingMode = .disallowed
        for (editor, label) in [(target as NSTextView, "Requested editor"), (other, "Independent editor")] {
            editor.isRichText = false
            editor.delegate = self
            editor.setAccessibilityLabel(label)
            container.addSubview(editor)
        }
        target.string = "seed"
        other.string = "GUARD"
        window.contentView!.addSubview(container)
        let menu = NSMenu(), edit = NSMenuItem(title: "Edit", action: nil, keyEquivalent: "")
        edit.submenu = NSMenu(title: "Edit")
        edit.submenu!.addItem(withTitle: "Paste", action: #selector(NSText.paste(_:)), keyEquivalent: "v")
        menu.addItem(edit)
        NSApp.mainMenu = menu
        window.orderBack(nil)
        window.makeKey()
        window.makeMain()
        window.makeFirstResponder(other)
        record()
        Timer.scheduledTimer(withTimeInterval: 0.04, repeats: true) { _ in MainActor.assumeIsolated { self.record() } }
    }
}

let app = FocusScopeApplication.shared
app.setActivationPolicy(.regular)
let delegate = FocusScopeDelegate()
app.delegate = delegate
app.run()
