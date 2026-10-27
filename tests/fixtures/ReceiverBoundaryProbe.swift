import AppKit

// An editable document contains an independent text input. A container's
// ancestry is not evidence that every nested responder edits that document.
// A passive text leaf can also be reported as the document's AX focus.
@MainActor final class BoundaryApplication: NSApplication {
    override func accessibilityApplicationFocusedUIElement() -> Any? {
        guard let host = delegate as? BoundaryDelegate else { return super.accessibilityApplicationFocusedUIElement() }
        if host.mode == "passive", host.window.firstResponder === host.owner { return host.leaf }
        return super.accessibilityApplicationFocusedUIElement()
    }
}

@MainActor final class BoundaryEditor: NSView {
    let name: String
    weak var host: BoundaryDelegate?
    var text = "", keys = 0, commits = 0, pastes = 0, focuses = 0
    init(_ name: String, frame: NSRect) {
        self.name = name
        super.init(frame: frame)
        setAccessibilityElement(true)
        setAccessibilityRole(name == "owner" ? .textArea : name == "combo" ? .comboBox : .textField)
        setAccessibilityIdentifier(name)
        setAccessibilityLabel(
            name == "owner" ? "Target document" : name == "combo" ? "Compound field" : "Input " + name)
    }
    required init?(coder: NSCoder) { fatalError() }
    override var acceptsFirstResponder: Bool { true }
    override func accessibilityValue() -> Any? { name == "combo" ? host?.comboChild.text : text }
    override func isAccessibilityFocused() -> Bool {
        guard let host else { return false }
        return window?.firstResponder === self && !(name == "owner" && host.mode == "passive")
    }
    override func setAccessibilityFocused(_ value: Bool) {
        if value {
            focuses += 1
            window?.makeFirstResponder(name == "combo" ? host?.comboChild : self)
            host?.record()
        }
    }
    override func isAccessibilitySelectorAllowed(_ selector: Selector) -> Bool {
        if ["setAccessibilityValue:", "setAccessibilitySelectedText:"].contains(NSStringFromSelector(selector)) {
            return false
        }
        return super.isAccessibilitySelectorAllowed(selector)
    }
    override func accessibilityAttributeNames() -> [NSAccessibility.Attribute] {
        super.accessibilityAttributeNames() + [NSAccessibility.Attribute(rawValue: "AXEditable")]
    }
    override func accessibilityAttributeValue(_ attribute: NSAccessibility.Attribute) -> Any? {
        if attribute.rawValue == "AXEditable" { return true }
        return super.accessibilityAttributeValue(attribute)
    }
    override func keyDown(with event: NSEvent) {
        guard let characters = event.characters, !event.modifierFlags.contains(.command) else { return }
        if characters == "\r" {
            commits += 1
        } else {
            text += characters
            keys += 1
            if name == "owner", host?.mode == "switch" { window?.makeFirstResponder(host!.child) }
        }
        host?.record()
    }
    @objc func paste(_ sender: Any?) {
        text += NSPasteboard.general.string(forType: .string) ?? ""
        pastes += 1
        host?.record()
    }
    override func draw(_ dirtyRect: NSRect) {
        NSColor.separatorColor.setStroke()
        NSBezierPath(rect: bounds.insetBy(dx: 1, dy: 1)).stroke()
        (name + ": " + text as NSString).draw(
            at: NSPoint(x: 8, y: bounds.height - 25), withAttributes: [.foregroundColor: NSColor.labelColor])
    }
}

@MainActor final class BoundaryLeaf: NSView {
    weak var host: BoundaryDelegate?
    override func accessibilityRole() -> NSAccessibility.Role? { .staticText }
    override func accessibilityValue() -> Any? { "Caret paragraph" }
    override func isAccessibilityFocused() -> Bool {
        host?.mode == "passive" && host?.window.firstResponder === host?.owner
    }
    override func isAccessibilitySelectorAllowed(_ selector: Selector) -> Bool {
        if ["setAccessibilityFocused:", "setAccessibilityValue:", "setAccessibilitySelectedText:"].contains(
            NSStringFromSelector(selector))
        {
            return false
        }
        return super.isAccessibilitySelectorAllowed(selector)
    }
}

@MainActor final class BoundaryDelegate: NSObject, NSApplicationDelegate {
    let directory = URL(fileURLWithPath: CommandLine.arguments[1])
    let owner = BoundaryEditor("owner", frame: NSRect(x: 15, y: 15, width: 460, height: 230))
    let child = BoundaryEditor("child", frame: NSRect(x: 20, y: 20, width: 410, height: 50))
    let leaf = BoundaryLeaf(frame: NSRect(x: 20, y: 90, width: 400, height: 40))
    let combo = BoundaryEditor("combo", frame: NSRect(x: 15, y: 265, width: 460, height: 65))
    let comboChild = BoundaryEditor("combo-child", frame: NSRect(x: 10, y: 10, width: 430, height: 40))
    var window: NSWindow!
    var mode = "independent", handled = ""
    var timer: Timer?
    func record() {
        owner.needsDisplay = true
        child.needsDisplay = true
        func facts(_ editor: BoundaryEditor) -> [String: Any] {
            [
                "text": editor.text, "keys": editor.keys, "commits": editor.commits, "pastes": editor.pastes,
                "focuses": editor.focuses, "focused": window.firstResponder === editor
            ]
        }
        let state: [String: Any] = [
            "owner": facts(owner), "child": facts(child), "combo": facts(comboChild), "mode": mode, "handled": handled,
            "pid": getpid(), "window": window.windowNumber
        ]
        try! JSONSerialization.data(withJSONObject: state, options: [.sortedKeys]).write(
            to: directory.appendingPathComponent("state.json"), options: .atomic)
    }
    func applicationDidFinishLaunching(_ notification: Notification) {
        window = NSWindow(
            contentRect: NSRect(x: 180, y: 180, width: 490, height: 350), styleMask: [.titled, .closable],
            backing: .buffered, defer: false)
        window.title = "Receiver boundaries"
        window.tabbingMode = .disallowed
        owner.host = self
        child.host = self
        leaf.host = self
        combo.host = self
        comboChild.host = self
        leaf.setAccessibilityElement(true)
        leaf.setAccessibilityIdentifier("passive-leaf")
        owner.addSubview(leaf)
        owner.addSubview(child)
        window.contentView!.addSubview(owner)
        combo.addSubview(comboChild)
        window.contentView!.addSubview(combo)
        let menu = NSMenu(), edit = NSMenu(title: "Edit"),
            item = NSMenuItem(title: "Edit", action: nil, keyEquivalent: "")
        edit.addItem(withTitle: "Paste", action: #selector(BoundaryEditor.paste(_:)), keyEquivalent: "v")
        item.submenu = edit
        menu.addItem(item)
        NSApp.mainMenu = menu
        window.orderBack(nil)
        window.makeKey()
        window.makeMain()
        window.makeFirstResponder(child)
        record()
        timer = Timer.scheduledTimer(withTimeInterval: 0.02, repeats: true) { [weak self] _ in
            MainActor.assumeIsolated {
                guard let self,
                    let command = try? String(
                        contentsOf: self.directory.appendingPathComponent("command"), encoding: .utf8)
                else { return }
                try? FileManager.default.removeItem(at: self.directory.appendingPathComponent("command"))
                self.handled = command
                self.mode = command
                self.window.makeFirstResponder(
                    command == "independent" ? self.child : command == "combo" ? self.comboChild : self.owner)
                self.record()
            }
        }
    }
}

let application = BoundaryApplication.shared
application.setActivationPolicy(.regular)
let delegate = BoundaryDelegate()
application.delegate = delegate
application.run()
