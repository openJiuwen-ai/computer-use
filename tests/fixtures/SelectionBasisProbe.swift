import AppKit

@MainActor final class Owner: NSView {
    override func accessibilityChildren() -> [Any]? { subviews }
    override func isAccessibilitySelectorAllowed(_ selector: Selector) -> Bool {
        if selector == #selector(setAccessibilityFocused(_:)) { return false }
        return super.isAccessibilitySelectorAllowed(selector)
    }
}

/// The native object and parent/child links never change. Only its declared
/// independent action changes, which makes it a different semantic boundary.
@MainActor final class Layout: NSView {
    var actionable = false
    override func accessibilityChildren() -> [Any]? { subviews }
    override func isAccessibilitySelectorAllowed(_ selector: Selector) -> Bool {
        if selector == #selector(accessibilityPerformPress) { return actionable }
        return super.isAccessibilitySelectorAllowed(selector)
    }
    override func accessibilityPerformPress() -> Bool { true }
}

@MainActor final class Field: NSTextField {
    var didFocus: (() -> Void)?
    var didSelect: ((NSRange) -> Void)?
    override func becomeFirstResponder() -> Bool {
        let accepted = super.becomeFirstResponder()
        if accepted { didFocus?() }
        return accepted
    }
    override func accessibilitySelectedTextRange() -> NSRange {
        currentEditor()?.selectedRange ?? super.accessibilitySelectedTextRange()
    }
    override func setAccessibilitySelectedTextRange(_ range: NSRange) {
        // NSTextField's inherited NSAccessibility setter can only override
        // its reported attribute. Keep this custom provider truthful: mutate
        // the actual field editor before injecting the semantic-boundary race.
        if let editor = currentEditor() {
            editor.selectedRange = range
        } else {
            super.setAccessibilitySelectedTextRange(range)
        }
        didSelect?(range)
    }
}

@MainActor final class Delegate: NSObject, NSApplicationDelegate {
    let output = URL(fileURLWithPath: CommandLine.arguments[1])
    var window: NSWindow!
    let owner = Owner(frame: NSRect(x: 20, y: 85, width: 380, height: 60))
    let layout = Layout(frame: NSRect(x: 5, y: 5, width: 365, height: 50))
    let field = Field(frame: NSRect(x: 5, y: 10, width: 350, height: 25))
    let other = NSTextField(frame: NSRect(x: 20, y: 30, width: 380, height: 25))
    var timer: Timer?
    var flipOnFocus = false
    var flipOnSelection = false
    var selectionChanges = 0
    var selectionEvents = [[String: String]]()
    var handled = ""

    func applicationDidFinishLaunching(_ note: Notification) {
        NSApp.setActivationPolicy(.regular)
        window = NSWindow(
            contentRect: NSRect(x: 160, y: 220, width: 420, height: 180),
            styleMask: [.titled, .closable, .miniaturizable], backing: .buffered, defer: false)
        window.title = "Selection basis fixture"
        owner.setAccessibilityElement(true)
        owner.setAccessibilityRole(.textField)
        owner.setAccessibilityIdentifier("basis-owner")
        layout.setAccessibilityElement(true)
        layout.setAccessibilityRole(.group)
        layout.setAccessibilityIdentifier("basis-layout")
        field.setAccessibilityIdentifier("basis-editor")
        field.stringValue = ""
        other.setAccessibilityIdentifier("basis-other")
        other.stringValue = "untouched"
        layout.addSubview(field)
        owner.addSubview(layout)
        window.contentView!.addSubview(owner)
        window.contentView!.addSubview(other)
        field.didFocus = { [weak self] in
            guard let self, self.flipOnFocus else { return }
            self.layout.actionable = true
            self.flipOnFocus = false
        }
        field.didSelect = { [weak self] range in
            guard let self else { return }
            self.selectionEvents.append([
                "requested": NSStringFromRange(range),
                "reported": NSStringFromRange(self.field.accessibilitySelectedTextRange()),
                "actual": self.field.currentEditor().map { NSStringFromRange($0.selectedRange) } ?? "no editor"
            ])
            guard self.flipOnSelection, range.length > 0 else { return }
            self.layout.actionable = true
            self.flipOnSelection = false
            self.selectionChanges += 1
        }
        window.orderBack(nil)
        window.makeFirstResponder(other)
        timer = Timer.scheduledTimer(withTimeInterval: 0.03, repeats: true) { [weak self] _ in
            Task { @MainActor in self?.tick() }
        }
        tick()
    }

    func tick() {
        let request = output.appendingPathComponent("command")
        if let command = try? String(contentsOf: request, encoding: .utf8) {
            try? FileManager.default.removeItem(at: request)
            switch command {
            case "independent": layout.actionable = true
            case "layout": layout.actionable = false
            case "content":
                field.stringValue = "formatted"
                field.currentEditor()?.string = "formatted"
                field.currentEditor()?.selectedRange = NSRange(location: 9, length: 0)
                layout.setAccessibilityLabel("updated visible label")
            case "arm":
                layout.actionable = false
                window.makeFirstResponder(other)
                flipOnFocus = true
            case "arm-selection":
                layout.actionable = false
                field.currentEditor()?.selectedRange = NSRange(location: 0, length: 0)
                flipOnSelection = true
            default: break
            }
            handled = command
        }
        let value: [String: Any] = [
            "text": field.currentEditor()?.string ?? field.stringValue,
            "other": other.currentEditor()?.string ?? other.stringValue, "actionable": layout.actionable,
            "editing": field.currentEditor() != nil, "selectionChanges": selectionChanges,
            "selectionEvents": selectionEvents, "handled": handled,
            "foreground": NSWorkspace.shared.frontmostApplication?.processIdentifier ?? -1,
            "contained": field.superview === layout && layout.superview === owner
        ]
        try! JSONSerialization.data(withJSONObject: value, options: [.sortedKeys]).write(
            to: output.appendingPathComponent("state.json"), options: .atomic)
    }
}

let app = NSApplication.shared
let delegate = Delegate()
app.delegate = delegate
app.run()
