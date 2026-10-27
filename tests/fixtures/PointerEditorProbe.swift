import AppKit

// Native focus is declared but ignored. Only a real mouse click establishes
// the caret. This models a provider defect without application-name branches.
@MainActor final class ClickOnlyEditor: NSTextView { override func setAccessibilityFocused(_ value: Bool) {} }

@MainActor final class PointerEditorDelegate: NSObject, NSApplicationDelegate, NSTextViewDelegate {
    let directory = Bundle.main.bundleURL.deletingLastPathComponent()
    var window: NSWindow!
    let editor = ClickOnlyEditor(frame: NSRect(x: 20, y: 80, width: 400, height: 120))
    let other = NSTextField(string: "untouched")
    func record() {
        try! JSONSerialization.data(withJSONObject: ["text": editor.string, "other": other.stringValue]).write(
            to: directory.appendingPathComponent("state.json"), options: .atomic)
    }
    func applicationDidFinishLaunching(_ notification: Notification) {
        window = NSWindow(
            contentRect: NSRect(x: 160, y: 150, width: 440, height: 230), styleMask: [.titled, .closable],
            backing: .buffered, defer: false)
        window.title = "Pointer editor preparation"
        window.tabbingMode = .disallowed
        let menu = NSMenu(), edit = NSMenu(title: "Edit"), item = NSMenuItem()
        edit.addItem(withTitle: "Paste", action: #selector(NSText.paste(_:)), keyEquivalent: "v")
        item.submenu = edit
        menu.addItem(item)
        NSApp.mainMenu = menu
        editor.isRichText = false
        editor.delegate = self
        editor.setAccessibilityLabel("Click-only editor")
        other.frame = NSRect(x: 20, y: 20, width: 400, height: 30)
        window.contentView!.addSubview(editor)
        window.contentView!.addSubview(other)
        window.orderBack(nil)
        window.makeKey()
        window.makeMain()
        window.makeFirstResponder(other)
        record()
    }
    func textDidChange(_ notification: Notification) { record() }
}

let app = NSApplication.shared
app.setActivationPolicy(.regular)
let delegate = PointerEditorDelegate()
app.delegate = delegate
app.run()
