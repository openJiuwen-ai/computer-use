import AppKit

// A composite advertises a writable value/focus, but its child is the real
// editor. Object setters returning success cannot prove these are equivalent.
@MainActor final class CompositeEditor: NSTextView {
    weak var host: CompositeInputDelegate?
    var commits = 0
    override func insertNewline(_ sender: Any?) {
        commits += 1
        host?.record()
    }
}

@MainActor final class CompositeOwner: NSView {
    let editor = CompositeEditor(frame: NSRect(x: 12, y: 12, width: 420, height: 64))
    weak var host: CompositeInputDelegate?
    var writes = 0, focuses = 0
    override func accessibilityRole() -> NSAccessibility.Role? { .comboBox }
    override func accessibilityValue() -> Any? { editor.string }
    override func setAccessibilityValue(_ value: Any?) {
        writes += 1
        host?.record()
    }
    override func isAccessibilityFocused() -> Bool { false }
    override func setAccessibilityFocused(_ value: Bool) {
        if value {
            focuses += 1
            host?.record()
        }
    }
    override func accessibilityChildren() -> [Any]? { [editor] }
    override func draw(_ rect: NSRect) {
        NSColor.separatorColor.setStroke()
        NSBezierPath(rect: bounds.insetBy(dx: 1, dy: 1)).stroke()
    }
}

@MainActor final class CompositeInputDelegate: NSObject, NSApplicationDelegate, NSTextViewDelegate {
    let directory = URL(fileURLWithPath: CommandLine.arguments[1])
    let owner = CompositeOwner(frame: NSRect(x: 20, y: 90, width: 445, height: 88))
    let other = NSTextField(string: "unrelated input")
    var window: NSWindow!
    func applicationDidFinishLaunching(_ notification: Notification) {
        window = NSWindow(
            contentRect: NSRect(x: 240, y: 220, width: 490, height: 215), styleMask: [.titled, .closable],
            backing: .buffered, defer: false)
        window.title = "Composite input contract"
        window.tabbingMode = .disallowed
        owner.host = self
        owner.setAccessibilityElement(true)
        owner.setAccessibilityIdentifier("compound")
        owner.setAccessibilityLabel("Compound input")
        owner.editor.host = self
        owner.editor.delegate = self
        owner.editor.string = "initial"
        owner.editor.setAccessibilityIdentifier("internal-editor")
        owner.editor.isRichText = false
        owner.editor.isAutomaticQuoteSubstitutionEnabled = false
        owner.addSubview(owner.editor)
        window.contentView!.addSubview(owner)
        other.frame = NSRect(x: 30, y: 35, width: 420, height: 28)
        other.setAccessibilityIdentifier("unrelated")
        window.contentView!.addSubview(other)
        let menu = NSMenu(), edit = NSMenu(title: "Edit"), item = NSMenuItem()
        edit.addItem(withTitle: "Select All", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")
        edit.addItem(withTitle: "Paste", action: #selector(NSText.paste(_:)), keyEquivalent: "v")
        item.submenu = edit
        menu.addItem(item)
        NSApp.mainMenu = menu
        window.orderBack(nil)
        window.makeKey()
        window.makeMain()
        window.makeFirstResponder(other)
        record()
    }
    func textDidChange(_ notification: Notification) { record() }
    func textViewDidChangeSelection(_ notification: Notification) { record() }
    func record() {
        guard window != nil else { return }
        let value: [String: Any] = [
            "text": owner.editor.string, "commits": owner.editor.commits, "ownerWrites": owner.writes,
            "ownerFocuses": owner.focuses, "editorFocused": window.firstResponder === owner.editor,
            "other": other.stringValue, "pid": getpid(), "window": window.windowNumber
        ]
        try! JSONSerialization.data(withJSONObject: value, options: [.sortedKeys]).write(
            to: directory.appendingPathComponent("state.json"), options: .atomic)
    }
}

let application = NSApplication.shared
application.setActivationPolicy(.regular)
let delegate = CompositeInputDelegate()
application.delegate = delegate
application.run()
