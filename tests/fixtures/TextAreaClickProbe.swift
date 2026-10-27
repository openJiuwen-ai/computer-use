import AppKit

// AX can report the intended responder while its window is not key. Paste
// follows the real window state, not the overridden accessibility report.
@MainActor final class ClickTextView: NSTextView {
    override func setAccessibilityFocused(_ value: Bool) { if value { window?.makeFirstResponder(self) } }
    override func paste(_ sender: Any?) { if window?.isKeyWindow == true { super.paste(sender) } }
}

@MainActor final class TextAreaApplication: NSApplication {
    override func sendEvent(_ event: NSEvent) {
        if [.leftMouseDown, .leftMouseUp, .keyDown].contains(event.type), let probe = delegate as? ProbeDelegate {
            let log = """
                event=\(event.type.rawValue) window=\(event.windowNumber) point=\(event.locationInWindow) \
                editor=\(probe.editor.frame) key=\(keyWindow?.windowNumber ?? 0)\n
                """
            let url = probe.directory.appendingPathComponent("events.txt")
            let prior = (try? String(contentsOf: url, encoding: .utf8)) ?? ""
            try? (prior + log).write(to: url, atomically: true, encoding: .utf8)
        }
        super.sendEvent(event)
    }
    override func accessibilityFocusedWindow() -> Any? { (delegate as? ProbeDelegate)?.window }
    override func accessibilityApplicationFocusedUIElement() -> Any? { (delegate as? ProbeDelegate)?.editor }
}

@MainActor final class ProbeDelegate: NSObject, NSApplicationDelegate, NSTextViewDelegate {
    var window: NSWindow!
    let editor = ClickTextView(frame: NSRect(x: 20, y: 70, width: 410, height: 190))
    let untouched = NSTextField(string: "unchanged")
    let directory = Bundle.main.bundleURL.deletingLastPathComponent()
    func record() {
        let data = try! JSONSerialization.data(
            withJSONObject: [
                "text": editor.string, "keyWindow": window.isKeyWindow, "untouched": untouched.stringValue
            ], options: [.sortedKeys])
        try! data.write(to: directory.appendingPathComponent("values.json"), options: .atomic)
    }
    func applicationDidFinishLaunching(_ notification: Notification) {
        try! String(getpid()).write(
            to: directory.appendingPathComponent("fixture.pid"), atomically: true, encoding: .utf8)
        window = NSWindow(
            contentRect: NSRect(x: 150, y: 150, width: 450, height: 300), styleMask: [.titled, .closable],
            backing: .buffered, defer: false)
        window.title = "Text area click probe"
        window.tabbingMode = .disallowed
        let menu = NSMenu(), application = NSMenuItem()
        application.submenu = NSMenu()
        application.submenu!.addItem(
            withTitle: "Quit", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        menu.addItem(application)
        let edit = NSMenuItem(title: "Edit", action: nil, keyEquivalent: "")
        let submenu = NSMenu(title: "Edit")
        submenu.addItem(withTitle: "Paste", action: #selector(NSText.paste(_:)), keyEquivalent: "v")
        edit.submenu = submenu
        menu.addItem(edit)
        NSApp.mainMenu = menu
        editor.isRichText = false
        editor.delegate = self
        editor.setAccessibilityIdentifier("editor")
        untouched.frame = NSRect(x: 20, y: 20, width: 410, height: 30)
        untouched.setAccessibilityIdentifier("untouched")
        window.contentView?.addSubview(editor)
        window.contentView?.addSubview(untouched)
        window.orderBack(nil)
        window.makeKey()
        window.makeMain()
        window.makeFirstResponder(editor)
        record()
    }
    func textDidChange(_ notification: Notification) { record() }
}

let app = TextAreaApplication.shared
app.setActivationPolicy(.regular)
let delegate = ProbeDelegate()
app.delegate = delegate
app.run()
