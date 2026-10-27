import AppKit

@MainActor final class ReadOnlyIntentField: NSTextField {
    override func accessibilityRole() -> NSAccessibility.Role? { .textField }
    override func accessibilityAttributeNames() -> [NSAccessibility.Attribute] {
        super.accessibilityAttributeNames() + [NSAccessibility.Attribute(rawValue: "AXEditable")]
    }
    override func accessibilityAttributeValue(_ attribute: NSAccessibility.Attribute) -> Any? {
        if attribute.rawValue == "AXEditable" { return false }
        return super.accessibilityAttributeValue(attribute)
    }
}

@MainActor final class SelectableIntentRow: NSView {
    weak var recorder: IntentProbe?
    var selected = false
    override func accessibilityRole() -> NSAccessibility.Role? { .row }
    override func accessibilityChildren() -> [Any]? { subviews }
    override func isAccessibilitySelected() -> Bool { selected }
    override func setAccessibilitySelected(_ value: Bool) {
        selected = value
        recorder?.record("row-selection")
    }
}

@MainActor final class IntentCell: NSView {
    override func accessibilityRole() -> NSAccessibility.Role? { .cell }
    override func accessibilityChildren() -> [Any]? { subviews }
}
@MainActor final class IntentLabel: NSView {
    var text = "Selectable row"
    override func accessibilityRole() -> NSAccessibility.Role? { .staticText }
    override func accessibilityValue() -> Any? { text }
    override func draw(_ rect: NSRect) {
        (text as NSString).draw(at: .zero, withAttributes: [.foregroundColor: NSColor.labelColor])
    }
}

@MainActor final class NoFrameIntentButton: NSView {
    weak var recorder: IntentProbe?
    override func accessibilityRole() -> NSAccessibility.Role? { .button }
    override func accessibilityFrame() -> NSRect { .zero }
    override func accessibilityPerformPress() -> Bool {
        recorder?.pressButton()
        return true
    }
    override func draw(_ rect: NSRect) {
        ("Zero frame button" as NSString).draw(
            at: NSPoint(x: 2, y: 4), withAttributes: [.foregroundColor: NSColor.labelColor])
    }
}

@MainActor final class LabeledIntentButton: NSView {
    weak var recorder: IntentProbe?
    override func accessibilityRole() -> NSAccessibility.Role? { .button }
    override func accessibilityChildren() -> [Any]? { subviews }
    override func accessibilityPerformPress() -> Bool {
        recorder?.pressButton()
        return true
    }
    override func draw(_ rect: NSRect) {
        NSColor.controlColor.setFill()
        NSBezierPath(roundedRect: bounds, xRadius: 5, yRadius: 5).fill()
    }
}

@MainActor final class GestureRegion: NSView {
    weak var recorder: IntentProbe?
    override var isFlipped: Bool { true }
    override func acceptsFirstMouse(for event: NSEvent?) -> Bool { true }
    override func accessibilityRole() -> NSAccessibility.Role? { .group }
    override func accessibilityPerformPress() -> Bool {
        recorder?.nativePresses += 1
        recorder?.record("native-press")
        return true
    }
    override func mouseDown(with event: NSEvent) { recorder?.record("left", count: event.clickCount) }
    override func rightMouseDown(with event: NSEvent) { recorder?.record("right", count: event.clickCount) }
    override func otherMouseDown(with event: NSEvent) { recorder?.record("middle", count: event.clickCount) }
    override func draw(_ rect: NSRect) {
        NSColor.controlBackgroundColor.setFill()
        bounds.fill()
        ("Gesture region" as NSString).draw(
            at: NSPoint(x: 20, y: 30), withAttributes: [.foregroundColor: NSColor.labelColor])
    }
}

@MainActor final class IntentProbe: NSObject, NSApplicationDelegate, NSTextFieldDelegate {
    var window: NSWindow!
    var fields = [String: NSTextField]()
    var submissions = [String: Int]()
    var nativePresses = 0
    var buttonPresses = 0
    let row = SelectableIntentRow()
    let counter = NSTextField(labelWithString: "Button presses: 0")
    let submitted = NSTextField(labelWithString: "No submission")
    var log: FileHandle!

    func record(_ kind: String, count: Int? = nil) {
        var data: [String: Any] = [
            "kind": kind, "values": fields.mapValues(\.stringValue), "submissions": submissions,
            "nativePresses": nativePresses, "buttonPresses": buttonPresses, "selected": row.selected,
            "foregroundPID": NSWorkspace.shared.frontmostApplication?.processIdentifier ?? 0
        ]
        if let count { data["clickCount"] = count }
        if let bytes = try? JSONSerialization.data(withJSONObject: data, options: [.sortedKeys]) {
            log.write(bytes)
            log.write(Data([10]))
            log.synchronizeFile()
        }
    }
    func applicationDidFinishLaunching(_ notification: Notification) {
        let path = Bundle.main.bundlePath.replacingOccurrences(of: ".app", with: ".events.jsonl")
        FileManager.default.createFile(atPath: path, contents: nil)
        log = FileHandle(forWritingAtPath: path)!
        window = NSWindow(
            contentRect: NSRect(x: 120, y: 150, width: 440, height: 420), styleMask: [.titled, .closable],
            backing: .buffered, defer: false)
        window.title = "Action Intent Probe"
        for (index, name) in ["first", "second"].enumerated() {
            let input = NSTextField(string: name)
            input.frame = NSRect(x: 24, y: 202 - index * 46, width: 380, height: 28)
            input.setAccessibilityIdentifier(name)
            input.delegate = self
            // Submission assertions count Return, not focus-loss callbacks.
            input.cell?.sendsActionOnEndEditing = false
            input.target = self
            input.action = #selector(submit(_:))
            fields[name] = input
            window.contentView!.addSubview(input)
        }
        let gesture = GestureRegion(frame: NSRect(x: 24, y: 34, width: 300, height: 90))
        gesture.setAccessibilityElement(true)
        gesture.setAccessibilityIdentifier("gesture")
        gesture.setAccessibilityLabel("Gesture region")
        gesture.recorder = self
        window.contentView!.addSubview(gesture)
        let button = LabeledIntentButton(frame: NSRect(x: 24, y: 366, width: 150, height: 28))
        button.setAccessibilityElement(true)
        button.setAccessibilityLabel("Count press")
        button.recorder = self
        let buttonLabel = IntentLabel(frame: NSRect(x: 12, y: 6, width: 125, height: 18))
        buttonLabel.text = "Count press"
        buttonLabel.setAccessibilityElement(true)
        button.addSubview(buttonLabel)
        window.contentView!.addSubview(button)
        let disabled = NSButton(title: "Disabled button", target: self, action: #selector(pressButton))
        disabled.isEnabled = false
        disabled.frame = NSRect(x: 215, y: 366, width: 180, height: 28)
        window.contentView!.addSubview(disabled)
        let readonly = ReadOnlyIntentField(string: "Read-only content")
        readonly.isEditable = false
        readonly.isSelectable = true
        readonly.setAccessibilityIdentifier("readonly")
        readonly.frame = NSRect(x: 24, y: 322, width: 220, height: 28)
        window.contentView!.addSubview(readonly)
        let slider = NSSlider(value: 0.5, minValue: 0, maxValue: 1, target: nil, action: nil)
        slider.setAccessibilityIdentifier("slider")
        slider.frame = NSRect(x: 255, y: 322, width: 140, height: 28)
        window.contentView!.addSubview(slider)
        row.frame = NSRect(x: 24, y: 274, width: 380, height: 28)
        row.setAccessibilityElement(true)
        let cell = IntentCell(frame: row.bounds)
        cell.setAccessibilityElement(true)
        let label = IntentLabel(frame: cell.bounds)
        label.setAccessibilityElement(true)
        cell.addSubview(label)
        row.addSubview(cell)
        row.recorder = self
        window.contentView!.addSubview(row)
        let noFrame = NoFrameIntentButton(frame: NSRect(x: 270, y: 274, width: 160, height: 28))
        noFrame.setAccessibilityElement(true)
        noFrame.setAccessibilityLabel("Zero frame button")
        noFrame.recorder = self
        window.contentView!.addSubview(noFrame)
        counter.frame = NSRect(x: 24, y: 244, width: 390, height: 20)
        submitted.frame = NSRect(x: 24, y: 8, width: 390, height: 20)
        window.contentView!.addSubview(counter)
        window.contentView!.addSubview(submitted)
        window.orderBack(nil)
        record("ready")
    }
    @objc func pressButton() {
        buttonPresses += 1
        counter.stringValue = "Button presses: \(buttonPresses)"
        record("button")
    }
    func controlTextDidChange(_ notification: Notification) {
        if let input = notification.object as? NSTextField, input === fields["second"], input.stringValue == "ali" {
            input.stringValue = "alice"
            (input.currentEditor() as? NSTextView)?.string = "alice"
        }
        record("text")
    }
    @objc func submit(_ sender: NSTextField) {
        if let name = fields.first(where: { $0.value === sender })?.key {
            submissions[name, default: 0] += 1
            submitted.stringValue = "Submitted \(name) (\(submissions[name]!)): \(sender.stringValue)"
        }
        record("submit")
    }
}

let app = NSApplication.shared
app.setActivationPolicy(.regular)
let delegate = IntentProbe()
app.delegate = delegate
app.run()
