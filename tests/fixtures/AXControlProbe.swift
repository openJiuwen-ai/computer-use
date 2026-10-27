import AppKit
import ApplicationServices

@MainActor final class ControlApplication: NSApplication {
    override func accessibilityApplicationFocusedUIElement() -> Any? {
        guard let delegate = delegate as? ControlDelegate else {
            return super.accessibilityApplicationFocusedUIElement()
        }
        for host in delegate.hosts where host.field.currentEditor() != nil {
            if host.mode == "shared" { return host.suggestions }
            return host
        }
        return super.accessibilityApplicationFocusedUIElement()
    }
}

@MainActor final class TextProxy: NSTextField {
    weak var host: ControlHost?
    override func setAccessibilityFocused(_ value: Bool) {
        // This proxy exposes text, but only the outer control can establish focus.
        host?.childFocusCalls += 1
        host?.changed?()
    }
    override func accessibilityParent() -> Any? { host?.mode == "forward" ? nil : super.accessibilityParent() }
    override func isAccessibilityFocused() -> Bool { host?.mode == "conflict" ? true : super.isAccessibilityFocused() }
    override func isAccessibilitySelectorAllowed(_ selector: Selector) -> Bool {
        // Keyboard editing works; direct text setters deliberately do not.
        if ["setAccessibilityValue:", "setAccessibilitySelectedText:"].contains(NSStringFromSelector(selector)) {
            return false
        }
        return super.isAccessibilitySelectorAllowed(selector)
    }
}

@MainActor final class ReadOnlyAXTextCell: NSTextFieldCell {
    // NSTextField's exposed cell still uses the legacy AX attribute bridge.
    override func accessibilityIsAttributeSettable(_ attribute: NSAccessibility.Attribute) -> Bool {
        if [.value, .selectedText].contains(attribute) { return false }
        return super.accessibilityIsAttributeSettable(attribute)
    }
    override func isAccessibilitySelectorAllowed(_ selector: Selector) -> Bool {
        if ["setAccessibilityValue:", "setAccessibilitySelectedText:"].contains(NSStringFromSelector(selector)) {
            return false
        }
        return super.isAccessibilitySelectorAllowed(selector)
    }
}

@MainActor final class Suggestions: NSView {
    weak var field: TextProxy?
    override func accessibilityRole() -> NSAccessibility.Role? { .list }
    override func accessibilitySharedFocusElements() -> [Any]? { field.map { [$0] } ?? [] }
}

@MainActor final class ControlHost: NSView {
    let mode: String
    var field: TextProxy!
    let suggestions = Suggestions(frame: .zero)
    var ownerFocusCalls = 0, childFocusCalls = 0, commits = 0
    var buttonPresses = 0
    var extraField: NSTextField?
    var changed: (() -> Void)?
    init(mode: String, frame: NSRect) {
        self.mode = mode
        super.init(frame: frame)
        setAccessibilityElement(true)
        setAccessibilityIdentifier(mode)
        setAccessibilityLabel("Control " + mode)
        makeField()
        if mode == "with-button" {
            field.frame.size.width -= 110
            let button = NSButton(title: "Independent", target: self, action: #selector(pressIndependent))
            button.frame = NSRect(x: bounds.width - 105, y: 0, width: 105, height: 28)
            addSubview(button)
        }
        if mode == "multiple" {
            field.frame.size.width = 210
            let other = NSTextField(string: "second untouched")
            other.frame = NSRect(x: 240, y: 0, width: 220, height: 28)
            other.setAccessibilityIdentifier("multiple-second")
            extraField = other
            addSubview(other)
        }
        suggestions.setAccessibilityElement(true)
        suggestions.setAccessibilityIdentifier(mode + "-suggestions")
        if mode == "shared" { addSubview(suggestions) }
        if mode == "conflict" {
            setAccessibilityFrame(.zero)
            field.setAccessibilityFrame(.zero)
        }
    }
    required init?(coder: NSCoder) { fatalError() }
    func makeField() {
        field?.removeFromSuperview()
        field = TextProxy(string: "before")
        field.cell = ReadOnlyAXTextCell(textCell: "before")
        field.isEditable = true
        field.isSelectable = true
        field.host = self
        field.frame = NSRect(x: 0, y: 0, width: bounds.width, height: 28)
        field.setAccessibilityIdentifier(mode + "-text")
        field.target = self
        field.action = #selector(submit)
        addSubview(field)
        suggestions.field = field
    }
    override func accessibilityRole() -> NSAccessibility.Role? { .comboBox }
    override func accessibilityIdentifier() -> String { mode }
    override func accessibilityChildren() -> [Any]? { subviews }
    override func accessibilityValue() -> Any? { field.stringValue }
    override func isAccessibilityFocused() -> Bool { field.currentEditor() != nil }
    override func setAccessibilityFocused(_ value: Bool) {
        guard value else { return }
        ownerFocusCalls += 1
        if mode != "conflict" {
            if mode == "recreated", ownerFocusCalls == 1 { makeField() }
            window?.makeKey()
            window?.makeFirstResponder(field)
        }
        changed?()
    }
    @objc func submit() {
        commits += 1
        changed?()
    }
    @objc func pressIndependent() {
        buttonPresses += 1
        changed?()
    }
}

@MainActor final class ControlDelegate: NSObject, NSApplicationDelegate {
    var window: NSWindow!
    var hosts = [ControlHost]()
    let other = NSTextField(string: "untouched")
    var directory: URL { Bundle.main.bundleURL.deletingLastPathComponent() }
    func record() {
        var value: [String: Any] = ["other": other.stringValue]
        for host in hosts {
            value[host.mode] = [
                "text": host.field.stringValue, "ownerFocus": host.ownerFocusCalls, "childFocus": host.childFocusCalls,
                "commits": host.commits, "buttonPresses": host.buttonPresses,
                "otherText": host.extraField?.stringValue ?? ""
            ]
        }
        try! JSONSerialization.data(withJSONObject: value, options: [.sortedKeys]).write(
            to: directory.appendingPathComponent("values.json"), options: .atomic)
    }
    func applicationDidFinishLaunching(_ notification: Notification) {
        try! String(getpid()).write(
            to: directory.appendingPathComponent("fixture.pid"), atomically: true, encoding: .utf8)
        window = NSWindow(
            contentRect: NSRect(x: 0, y: 0, width: 520, height: 580), styleMask: [.titled, .closable],
            backing: .buffered, defer: false)
        window.title = "AX control resolution"
        window.tabbingMode = .disallowed
        window.isReleasedWhenClosed = false
        for (index, mode) in ["outer", "forward", "shared", "recreated", "conflict", "with-button", "multiple"]
            .enumerated()
        {
            let host = ControlHost(mode: mode, frame: NSRect(x: 20, y: 495 - index * 65, width: 470, height: 35))
            host.changed = { [weak self] in self?.record() }
            hosts.append(host)
            window.contentView?.addSubview(host)
        }
        other.frame = NSRect(x: 20, y: 20, width: 470, height: 28)
        other.setAccessibilityIdentifier("other")
        window.contentView?.addSubview(other)
        NotificationCenter.default.addObserver(forName: NSControl.textDidChangeNotification, object: nil, queue: .main)
        { [weak self] _ in MainActor.assumeIsolated { self?.record() } }
        window.center()
        window.orderBack(nil)
        window.makeKey()
        window.makeMain()
        window.makeFirstResponder(other)
        record()
    }
}
// Independent out-of-process AX inspection: the UI test must prove that text
// setters are actually unavailable through AX, not just trust this fixture.
if CommandLine.arguments.count == 3, CommandLine.arguments[1] == "--capabilities",
    let pid = pid_t(CommandLine.arguments[2])
{
    var found = [[String: Any]](), visited = [AXUIElement]()
    func read(_ e: AXUIElement, _ key: String) -> CFTypeRef? {
        var value: CFTypeRef?
        return AXUIElementCopyAttributeValue(e, key as CFString, &value) == .success ? value : nil
    }
    func visit(_ e: AXUIElement) {
        guard visited.count < 200, !visited.contains(where: { CFEqual($0, e) }) else { return }
        visited.append(e)
        if read(e, kAXRoleAttribute) as? String == kAXTextFieldRole, let id = read(e, kAXIdentifierAttribute) as? String
        {
            var row: [String: Any] = ["id": id]
            for key in [kAXValueAttribute, kAXSelectedTextAttribute] {
                var writable = DarwinBoolean(false)
                let status = AXUIElementIsAttributeSettable(e, key as CFString, &writable)
                row[key] = ["status": status.rawValue, "writable": writable.boolValue]
            }
            found.append(row)
        }
        for child in read(e, kAXChildrenAttribute) as? [AXUIElement] ?? [] { visit(child) }
    }
    visit(AXUIElementCreateApplication(pid))
    print(String(data: try! JSONSerialization.data(withJSONObject: found, options: [.sortedKeys]), encoding: .utf8)!)
    exit(0)
}
let application = ControlApplication.shared
application.setActivationPolicy(.regular)
let delegate = ControlDelegate()
application.delegate = delegate
application.run()
