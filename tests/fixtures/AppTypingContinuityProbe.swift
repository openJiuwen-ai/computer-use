import AppKit

// Two real NSTextView editing buffers share one window. Moving firstResponder
// after textDidChange models an ordinary same-window form transition; only
// actual AppKit text events change the buffers, never fixture arrangements.
@MainActor final class TypingApplication: NSApplication {
    override func accessibilityApplicationFocusedUIElement() -> Any? {
        if let editor = keyWindow?.firstResponder as? TypingEditor { return editor }
        return super.accessibilityApplicationFocusedUIElement()
    }
}

@MainActor final class TypingEditor: NSTextView {
    let name: String
    private let ownedTextStorage: NSTextStorage
    weak var host: TypingDelegate?

    init(_ name: String, frame: NSRect) {
        self.name = name
        let storage = NSTextStorage()
        let layout = NSLayoutManager()
        let container = NSTextContainer(containerSize: NSSize(width: frame.width, height: .greatestFiniteMagnitude))
        storage.addLayoutManager(layout)
        layout.addTextContainer(container)
        ownedTextStorage = storage
        // Unlike init(frame:), the designated initializer does not create a
        // text network for a nil container. Supply a complete native network.
        super.init(frame: frame, textContainer: container)
        isRichText = false
        isEditable = true
        isSelectable = true
        isAutomaticQuoteSubstitutionEnabled = false
        isAutomaticDashSubstitutionEnabled = false
        setAccessibilityIdentifier(name)
        setAccessibilityLabel(name)
        font = NSFont.monospacedSystemFont(ofSize: 17, weight: .regular)
    }
    required init?(coder: NSCoder) { fatalError("init(coder:) not implemented") }
    var textSystemFacts: [String: Bool] {
        [
            "hasTextStorage": textStorage != nil, "hasLayoutManager": layoutManager != nil,
            "hasTextContainer": textContainer != nil, "storageOwned": textStorage === ownedTextStorage,
            "layoutAttached": layoutManager?.textStorage === ownedTextStorage,
            "containerAttached": textContainer?.layoutManager === layoutManager,
            "viewAttached": textContainer?.textView === self
        ]
    }
    override func isAccessibilityFocused() -> Bool { window?.firstResponder === self }
    override func insertTab(_ sender: Any?) { window?.selectNextKeyView(self) }
    override func keyDown(with event: NSEvent) {
        host?.key(self, event, pressed: true)
        super.keyDown(with: event)
        host?.record("keyDown-complete")
    }
    override func keyUp(with event: NSEvent) {
        host?.key(self, event, pressed: false)
        super.keyUp(with: event)
    }
}

@MainActor final class TypingDelegate: NSObject, NSApplicationDelegate, NSTextViewDelegate {
    let directory = URL(fileURLWithPath: CommandLine.arguments[1])
    let first = TypingEditor("First input", frame: NSRect(x: 20, y: 120, width: 460, height: 65))
    let second = TypingEditor("Second input", frame: NSRect(x: 20, y: 25, width: 460, height: 65))
    let other = TypingEditor("Other window input", frame: NSRect(x: 20, y: 25, width: 350, height: 65))
    var a: NSWindow!, b: NSWindow!
    var changes = ["First input": 0, "Second input": 0, "Other window input": 0]
    var events = [[String: Any]]()
    var armed = false, handled = ""
    var timer: Timer?

    func record(_ reason: String) {
        guard a != nil, b != nil else { return }
        let fields = Dictionary(
            uniqueKeysWithValues: [first, second, other].map { editor in
                let range = editor.selectedRange()
                return (
                    editor.name,
                    [
                        "text": editor.string, "changes": changes[editor.name]!,
                        "focused": editor.window?.firstResponder === editor,
                        "selection": [range.location, range.length], "textSystem": editor.textSystemFacts
                    ] as [String: Any]
                )
            })
        let state: [String: Any] = [
            "reason": reason, "pid": getpid(), "handled": handled, "armed": armed, "fields": fields, "events": events,
            "windows": ["a": a.windowNumber, "b": b.windowNumber], "keyWindow": NSApp.keyWindow?.title ?? "",
            "foregroundPID": NSWorkspace.shared.frontmostApplication?.processIdentifier ?? 0
        ]
        try! JSONSerialization.data(withJSONObject: state, options: [.sortedKeys]).write(
            to: directory.appendingPathComponent("state.json"), options: .atomic)
    }
    func key(_ editor: TypingEditor, _ event: NSEvent, pressed: Bool) {
        events.append([
            "editor": editor.name, "kind": pressed ? "press" : "release", "characters": event.characters ?? "",
            "keyCode": event.keyCode, "window": event.windowNumber, "timestamp": event.timestamp,
            "modifiers": event.modifierFlags.rawValue
        ])
        record("key")
    }
    func textDidChange(_ notification: Notification) {
        guard let editor = notification.object as? TypingEditor else { return }
        changes[editor.name, default: 0] += 1
        if editor === first, armed {
            armed = false
            a.makeFirstResponder(second)
        }
        record("textDidChange")
    }
    func applicationDidFinishLaunching(_ notification: Notification) {
        func window(_ title: String, frame: NSRect) -> NSWindow {
            let result = NSWindow(contentRect: frame, styleMask: [.titled, .closable], backing: .buffered, defer: false)
            result.title = title
            result.tabbingMode = .disallowed
            result.isReleasedWhenClosed = false
            result.orderBack(nil)
            return result
        }
        a = window("App typing A", frame: NSRect(x: 100, y: 180, width: 500, height: 220))
        b = window("App typing B", frame: NSRect(x: 620, y: 180, width: 390, height: 120))
        for editor in [first, second, other] {
            editor.host = self
            editor.delegate = self
            (editor === other ? b : a)?.contentView?.addSubview(editor)
        }
        first.nextKeyView = second
        second.nextKeyView = first
        other.string = "untouched"
        b.makeFirstResponder(other)
        a.makeKey()
        a.makeMain()
        a.makeFirstResponder(first)
        record("ready")
        timer = Timer.scheduledTimer(withTimeInterval: 0.02, repeats: true) { [weak self] _ in
            MainActor.assumeIsolated {
                guard let self,
                    let command = try? String(
                        contentsOf: self.directory.appendingPathComponent("command"), encoding: .utf8)
                else { return }
                try? FileManager.default.removeItem(at: self.directory.appendingPathComponent("command"))
                precondition(command == "arm-first")
                self.a.makeFirstResponder(self.first)
                self.first.setSelectedRange(NSRange(location: self.first.string.utf16.count, length: 0))
                self.armed = true
                self.handled = command
                self.record("armed")
            }
        }
    }
}

let application = TypingApplication.shared
application.setActivationPolicy(.regular)
let delegate = TypingDelegate()
application.delegate = delegate
application.run()
