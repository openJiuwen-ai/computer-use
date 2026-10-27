import AppKit

// One real text editor is reused by two semantic controls. The active owner
// deliberately omits it from AXChildren, but the editor reports its real parent.
// The file oracle reads application state; production automation cannot use it.
@MainActor final class ReverseOwner: NSView {
    let name: String
    weak var host: ReverseEditorDelegate?
    init(_ name: String, frame: NSRect) {
        self.name = name
        super.init(frame: frame)
        setAccessibilityElement(true)
        setAccessibilityRole(.comboBox)
        setAccessibilityIdentifier("reverse-owner-" + name)
        setAccessibilityLabel("Shared editor " + name)
    }
    required init?(coder: NSCoder) { fatalError("init(coder:) not implemented") }
    override func accessibilityIdentifier() -> String { "reverse-owner-" + name }
    override func accessibilityChildren() -> [Any]? { [] }
    override func accessibilityValue() -> Any? { nil }
    override func isAccessibilitySelectorAllowed(_ selector: Selector) -> Bool {
        if selector == #selector(setAccessibilityValue(_:)) || selector == #selector(setAccessibilitySelectedText(_:)) {
            return false
        }
        return super.isAccessibilitySelectorAllowed(selector)
    }
    override func isAccessibilityFocused() -> Bool { false }
    override func setAccessibilityFocused(_ value: Bool) { if value { host?.select(self) } }
    override func draw(_ dirtyRect: NSRect) {
        NSColor.separatorColor.setStroke()
        NSBezierPath(rect: bounds.insetBy(dx: 1, dy: 1)).stroke()
    }
}

@MainActor final class ReverseTextView: NSTextView {
    var beforeSelection: (() -> Void)?
    override func setAccessibilitySelectedTextRange(_ range: NSRange) {
        super.setAccessibilitySelectedTextRange(range)
        let callback = beforeSelection
        beforeSelection = nil
        callback?()
    }
}

@MainActor final class ReverseEditorDelegate: NSObject, NSApplicationDelegate, NSTextViewDelegate {
    let directory = URL(fileURLWithPath: CommandLine.arguments[1])
    var window: NSWindow!
    let first = ReverseOwner("A", frame: NSRect(x: 20, y: 120, width: 400, height: 65))
    let second = ReverseOwner("B", frame: NSRect(x: 20, y: 25, width: 400, height: 65))
    let editor = ReverseTextView(frame: NSRect(x: 8, y: 8, width: 384, height: 49))
    var active: ReverseOwner?
    var stored = ["A": "", "B": "untouched"]
    var handled = ""
    var changes = 0
    var timer: Timer?

    func select(_ owner: ReverseOwner) {
        if active !== owner {
            if let active { stored[active.name] = editor.string }
            editor.removeFromSuperview()
            owner.addSubview(editor)
            active = owner
            editor.string = stored[owner.name]!
        }
        window.makeFirstResponder(editor)
        editor.setSelectedRange(NSRange(location: editor.string.utf16.count, length: 0))
        record()
    }

    func record() {
        var values = stored
        if let active { values[active.name] = editor.string }
        let state: [String: Any] = [
            "values": values, "active": active?.name ?? "", "changes": changes, "handled": handled,
            "editorFocused": window.firstResponder === editor, "pid": getpid(), "window": window.windowNumber
        ]
        try! JSONSerialization.data(withJSONObject: state, options: [.sortedKeys]).write(
            to: directory.appendingPathComponent("state.json"), options: .atomic)
    }

    func textDidChange(_ notification: Notification) {
        changes += 1
        record()
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        let menu = NSMenu(), edit = NSMenu(title: "Edit")
        edit.addItem(withTitle: "Paste", action: #selector(NSText.paste(_:)), keyEquivalent: "v")
        edit.addItem(withTitle: "Select All", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")
        let item = NSMenuItem(title: "Edit", action: nil, keyEquivalent: "")
        item.submenu = edit
        menu.addItem(item)
        NSApp.mainMenu = menu
        window = NSWindow(
            contentRect: NSRect(x: 160, y: 120, width: 440, height: 220), styleMask: [.titled, .closable],
            backing: .buffered, defer: false)
        window.title = "Reverse editor ownership"
        window.tabbingMode = .disallowed
        for owner in [first, second] {
            owner.host = self
            window.contentView!.addSubview(owner)
        }
        editor.isRichText = false
        editor.delegate = self
        editor.setAccessibilityIdentifier("shared-live-editor")
        window.orderBack(nil)
        window.makeKey()
        window.makeMain()
        select(first)
        timer = Timer.scheduledTimer(withTimeInterval: 0.02, repeats: true) { [weak self] _ in
            MainActor.assumeIsolated { self?.command() }
        }
    }

    func command() {
        let path = directory.appendingPathComponent("command")
        guard let value = try? String(contentsOf: path, encoding: .utf8) else { return }
        try? FileManager.default.removeItem(at: path)
        handled = value
        switch value {
        case "A": select(first)
        case "B": select(second)
        case "arm-switch":
            editor.beforeSelection = { [weak self] in
                guard let self else { return }
                self.select(self.second)
            }
        default: break
        }
        record()
    }
}

let application = NSApplication.shared
application.setActivationPolicy(.regular)
let delegate = ReverseEditorDelegate()
application.delegate = delegate
application.run()
