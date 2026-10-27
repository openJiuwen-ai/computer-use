import AppKit

// The real key recipient stays in an editor. AX deliberately reports its active
// suggestion instead, as a combobox/aria-activedescendant provider may do.
// Only the harness reads state.json; production automation uses external AX.
@MainActor final class VirtualApplication: NSApplication {
    override func accessibilityApplicationFocusedUIElement() -> Any? {
        guard let host = delegate as? VirtualDelegate, host.window?.firstResponder === host.editor else {
            return super.accessibilityApplicationFocusedUIElement()
        }
        if host.dynamic && host.keys == 0 { return host.editor }
        return host.popup.items[host.selected]
    }
}

@MainActor final class VirtualSuggestion: NSView {
    let index: Int
    weak var host: VirtualDelegate?
    init(_ index: Int) {
        self.index = index
        super.init(frame: NSRect(x: 0, y: index * 30, width: 350, height: 28))
        setAccessibilityElement(true)
        setAccessibilityRole(.row)
        setAccessibilityIdentifier("suggestion-\(index)")
        setAccessibilityLabel("Suggestion \(index)")
    }
    required init?(coder: NSCoder) { fatalError() }
    override func isAccessibilityFocused() -> Bool { host?.selected == index }
    override func isAccessibilitySelected() -> Bool { host?.selected == index }
}

@MainActor final class VirtualPopup: NSView {
    weak var host: VirtualDelegate?
    let items = [VirtualSuggestion(0), VirtualSuggestion(1)]
    override func accessibilitySelectedChildren() -> [Any]? {
        guard let host else { return [] }
        return host.mode == "multiple" ? items : [items[host.selected]]
    }
    override func accessibilityChildren() -> [Any]? { items }
}

@MainActor final class VirtualEditor: NSView {
    weak var host: VirtualDelegate?
    var value = ""
    override var acceptsFirstResponder: Bool { true }
    override func accessibilityRole() -> NSAccessibility.Role? {
        CommandLine.arguments.contains("--textarea") ? .textArea : .comboBox
    }
    override func accessibilityValue() -> Any? { value }
    override func isAccessibilityFocused() -> Bool { host.map { $0.dynamic && $0.keys == 0 } == true }
    override func setAccessibilityFocused(_ value: Bool) {
        if value {
            window?.makeFirstResponder(self)
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
        Array(
            Set(
                super.accessibilityAttributeNames()
                    + ["AXHasPopup", "AXOwns", "AXLinkedUIElements", "AXEditable"].map(
                        NSAccessibility.Attribute.init(rawValue:))))
    }
    override func accessibilityAttributeValue(_ attribute: NSAccessibility.Attribute) -> Any? {
        if ["AXHasPopup", "AXEditable"].contains(attribute.rawValue) { return true }
        if attribute.rawValue == "AXOwns" {
            return MainActor.assumeIsolated { () -> [VirtualPopup] in
                host.map { ["unlinked", "linked"].contains($0.mode) ? [] : [$0.popup] } ?? []
            }
        }
        if attribute.rawValue == "AXLinkedUIElements" {
            return MainActor.assumeIsolated { () -> [VirtualPopup] in
                host.map { $0.mode == "linked" ? [$0.popup] : [] } ?? []
            }
        }
        return super.accessibilityAttributeValue(attribute)
    }
    override func keyDown(with event: NSEvent) {
        guard let text = event.characters, !event.modifierFlags.contains(.command) else { return }
        if text == "\r" {
            host?.commits += 1
        } else {
            value += text
            host?.keys += 1
            if let host {
                host.selected = 1 - host.selected
                if host.dynamic { host.popup.isHidden = false }
                if host.mode == "switch" { window?.makeFirstResponder(host.other) }
            }
        }
        host?.record()
    }
    @objc func paste(_ sender: Any?) {
        value += NSPasteboard.general.string(forType: .string) ?? ""
        host?.pastes += 1
        host?.record()
    }
}

@MainActor final class VirtualDelegate: NSObject, NSApplicationDelegate {
    let directory = URL(fileURLWithPath: CommandLine.arguments[1])
    let editor = VirtualEditor(frame: NSRect(x: 20, y: 135, width: 360, height: 32))
    let popup = VirtualPopup(frame: NSRect(x: 20, y: 60, width: 360, height: 65))
    let other = NSTextField(string: "untouched")
    var window: NSWindow!
    var selected = 0, keys = 0, pastes = 0, commits = 0
    let dynamic = CommandLine.arguments.contains("--dynamic")
    var mode = "normal", handled = ""
    var timer: Timer?
    func record() {
        let state: [String: Any] = [
            "text": editor.value, "other": other.stringValue, "selected": selected, "keys": keys, "pastes": pastes,
            "commits": commits, "mode": mode, "handled": handled, "editorFocused": window.firstResponder === editor,
            "pid": getpid(), "window": window.windowNumber, "dynamic": dynamic,
            "keyWindow": NSApp.keyWindow?.windowNumber ?? 0,
            "frontmostPID": NSWorkspace.shared.frontmostApplication?.processIdentifier ?? 0
        ]
        try! JSONSerialization.data(withJSONObject: state, options: [.sortedKeys]).write(
            to: directory.appendingPathComponent("state.json"), options: .atomic)
    }
    func applicationDidFinishLaunching(_ notification: Notification) {
        window = NSWindow(
            contentRect: NSRect(x: 200, y: 200, width: 410, height: 210), styleMask: [.titled, .closable],
            backing: .buffered, defer: false)
        window.title = "Virtual focus input"
        window.tabbingMode = .disallowed
        editor.host = self
        editor.setAccessibilityElement(true)
        editor.setAccessibilityIdentifier("virtual-editor")
        editor.setAccessibilityLabel("Search entry")
        popup.host = self
        popup.setAccessibilityElement(true)
        popup.setAccessibilityRole(.list)
        popup.isHidden = dynamic
        popup.setAccessibilityIdentifier("suggestions")
        for item in popup.items {
            item.host = self
            popup.addSubview(item)
        }
        other.frame = NSRect(x: 20, y: 15, width: 360, height: 30)
        other.setAccessibilityIdentifier("other")
        for view in [editor, popup, other] { window.contentView!.addSubview(view) }
        let menu = NSMenu(), edit = NSMenu(title: "Edit"),
            item = NSMenuItem(title: "Edit", action: nil, keyEquivalent: "")
        edit.addItem(withTitle: "Paste", action: #selector(VirtualEditor.paste(_:)), keyEquivalent: "v")
        item.submenu = edit
        menu.addItem(item)
        NSApp.mainMenu = menu
        window.orderBack(nil)
        window.makeKey()
        window.makeMain()
        window.makeFirstResponder(editor)
        if dynamic {
            window.makeKeyAndOrderFront(nil)
            NSApp.activate()
        }
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
                self.window.makeFirstResponder(self.editor)
                self.record()
            }
        }
    }
}

let application = VirtualApplication.shared
application.setActivationPolicy(.regular)
let delegate = VirtualDelegate()
application.delegate = delegate
application.run()
