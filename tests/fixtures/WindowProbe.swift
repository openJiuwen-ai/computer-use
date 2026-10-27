import AppKit

@MainActor final class DocumentWindow: NSWindow {
    weak var recorder: WindowProbe?
    override func setAccessibilityMain(_ main: Bool) {
        super.setAccessibilityMain(main)
        if main, let name = identifier?.rawValue { recorder?.menuDocument = name }
    }
}

// A menu provider may bind its command objects to the document current when
// children are queried. Old objects keep that receiver after AXMain changes.
// AppKit invokes this accessibility callback on the fixture's main thread;
// assumeIsolated asserts that contract before touching native menu state.
@MainActor final class DocumentMenu: NSMenu, @unchecked Sendable {
    var receiver: String?
    var retainedItems = [NSMenuItem]()
    var duplicateCommands = false
    weak var recorder: WindowProbe?
    override func accessibilityChildren() -> [Any]? {
        MainActor.assumeIsolated {
            let current = recorder?.menuDocument ?? "none"
            if current != receiver {
                receiver = current
                removeAllItems()
                for _ in 0..<(duplicateCommands ? 2 : 1) {
                    let item = NSMenuItem(
                        title: duplicateCommands ? "Record Duplicate Document" : "Record Current Document",
                        action: #selector(WindowProbe.recordMenu(_:)), keyEquivalent: "")
                    item.target = recorder
                    item.representedObject = current
                    retainedItems.append(item)
                    addItem(item)
                }
            }
        }
        return super.accessibilityChildren()
    }
}

// Disposable multi-window application. It never activates the system foreground.
@MainActor final class WindowProbe: NSObject, NSApplicationDelegate, NSTextFieldDelegate {
    var first: NSWindow!
    var second: NSWindow!
    var panel: NSPanel?
    var inputs = [String: NSTextField]()
    var submissions = [String: Int]()
    var log: FileHandle!
    var recordCommand: NSButton!
    var recordActions = 0
    // Native background activation can clear NSApp.mainWindow; retain the
    // document selected through the native accessibility main-window setter.
    var menuDocument = "Document Alpha"

    func record(_ kind: String, receiver: String? = nil) {
        var values: [String: Any] = [
            "kind": kind, "timestampMs": Date().timeIntervalSince1970 * 1000, "values": inputs.mapValues(\.stringValue),
            "submissions": submissions, "recordActions": recordActions,
            "keyWindow": NSApp.keyWindow?.identifier?.rawValue ?? "none",
            "foregroundPID": NSWorkspace.shared.frontmostApplication?.processIdentifier ?? 0
        ]
        if let receiver { values["receiver"] = receiver }
        try? log.write(contentsOf: JSONSerialization.data(withJSONObject: values) + Data([10]))
    }

    func makeWindow(title: String, label: String, frame: NSRect, color: NSColor) -> NSWindow {
        let window = DocumentWindow(
            contentRect: frame, styleMask: [.titled, .closable], backing: .buffered, defer: false)
        window.recorder = self
        window.title = title
        window.isReleasedWhenClosed = false
        window.backgroundColor = color
        window.identifier = NSUserInterfaceItemIdentifier(label)
        let text = NSTextField(labelWithString: label)
        text.frame = NSRect(x: 20, y: 70, width: 260, height: 24)
        window.contentView!.addSubview(text)
        let input = NSTextField(string: label)
        input.frame = NSRect(x: 20, y: 105, width: 260, height: 24)
        input.setAccessibilityIdentifier("document-input")
        // This fixture counts explicit Return submissions, not AppKit's
        // end-editing actions during window construction or focus changes.
        input.cell?.sendsActionOnEndEditing = false
        input.delegate = self
        input.target = self
        input.action = #selector(submit(_:))
        window.contentView!.addSubview(input)
        inputs[label] = input
        window.orderBack(nil)
        return window
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        let url = Bundle.main.bundleURL.deletingPathExtension().appendingPathExtension("events.jsonl")
        FileManager.default.createFile(atPath: url.path, contents: nil)
        log = try! FileHandle(forWritingTo: url)
        let menu = NSMenu()
        let app = NSMenuItem()
        app.submenu = NSMenu()
        app.submenu!.addItem(withTitle: "Quit", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        menu.addItem(app)
        let file = NSMenuItem(title: "File", action: nil, keyEquivalent: "")
        file.submenu = NSMenu(title: "File")
        let item = file.submenu!.addItem(withTitle: "Show Export", action: #selector(showPanel), keyEquivalent: "")
        item.target = self
        let commands = file.submenu!.addItem(withTitle: "Document Actions", action: nil, keyEquivalent: "")
        let documentMenu = DocumentMenu(title: "Document Actions")
        documentMenu.autoenablesItems = false
        documentMenu.recorder = self
        commands.submenu = documentMenu
        let duplicateCommands = file.submenu!.addItem(withTitle: "Duplicate Actions", action: nil, keyEquivalent: "")
        let duplicateMenu = DocumentMenu(title: "Duplicate Actions")
        duplicateMenu.autoenablesItems = false
        duplicateMenu.recorder = self
        duplicateMenu.duplicateCommands = true
        duplicateCommands.submenu = duplicateMenu
        menu.addItem(file)
        // Standard AppKit shortcuts are menu commands delivered through the
        // responder chain; an application without an Edit menu has no Cmd-V
        // key equivalent. Use native actions, not a fixture paste handler.
        let edit = NSMenuItem(title: "Edit", action: nil, keyEquivalent: "")
        edit.submenu = NSMenu(title: "Edit")
        for (title, action, key) in [
            ("Cut", "cut:", "x"), ("Copy", "copy:", "c"), ("Paste", "paste:", "v"), ("Select All", "selectAll:", "a")
        ] { edit.submenu!.addItem(withTitle: title, action: Selector(action), keyEquivalent: key) }
        menu.addItem(edit)
        NSApp.mainMenu = menu
        first = makeWindow(
            title: "Same title", label: "Document Alpha", frame: NSRect(x: 70, y: 100, width: 420, height: 180),
            color: .systemBlue)
        second = makeWindow(
            title: "Same title", label: "Document Beta", frame: NSRect(x: 300, y: 100, width: 340, height: 150),
            color: .systemRed)
        let button = NSButton(title: "Show Export", target: self, action: #selector(showPanel))
        button.frame = NSRect(x: 20, y: 25, width: 130, height: 28)
        first.contentView!.addSubview(button)
        first.representedURL = Bundle.main.bundleURL.deletingLastPathComponent().appendingPathComponent(
            "document-one.txt")
        recordCommand = NSButton(title: "Open record Alpha", target: self, action: #selector(openRecord))
        recordCommand.frame = NSRect(x: 170, y: 25, width: 200, height: 28)
        first.contentView!.addSubview(recordCommand)
        for (title, action, x) in [
            ("Change record", #selector(changeRecord), 20.0), ("Change document", #selector(changeDocument), 170.0)
        ] {
            let control = NSButton(title: title, target: self, action: action)
            control.frame = NSRect(x: x, y: 25, width: 150, height: 28)
            second.contentView!.addSubview(control)
        }
        first.makeKey()
        first.makeMain()
        first.makeFirstResponder(inputs["Document Alpha"])
        let seed = NSMenuItem(title: "Record Current Document", action: #selector(recordMenu(_:)), keyEquivalent: "")
        seed.target = self
        seed.representedObject = "Document Alpha"
        documentMenu.receiver = "Document Alpha"
        documentMenu.retainedItems.append(seed)
        documentMenu.addItem(seed)
        duplicateMenu.receiver = "Document Alpha"
        for _ in 0..<2 {
            let item = NSMenuItem(
                title: "Record Duplicate Document", action: #selector(recordMenu(_:)), keyEquivalent: "")
            item.target = self
            item.representedObject = "Document Alpha"
            duplicateMenu.retainedItems.append(item)
            duplicateMenu.addItem(item)
        }
        record("ready")
    }

    func controlTextDidChange(_ notification: Notification) {
        let field = notification.object as? NSTextField
        record("text-change", receiver: field?.window?.identifier?.rawValue)
    }

    @objc func submit(_ sender: NSTextField) {
        let name = sender.window!.identifier!.rawValue
        submissions[name, default: 0] += 1
        record("submit", receiver: name)
    }

    @objc func openRecord() {
        recordActions += 1
        record("open-record")
    }
    @objc func recordMenu(_ sender: NSMenuItem) {
        record("menu-command", receiver: sender.representedObject as? String)
    }
    @objc func changeRecord() {
        recordCommand.title = "Open record Beta"
        record("change-record")
    }
    @objc func changeDocument() {
        first.representedURL = Bundle.main.bundleURL.deletingLastPathComponent().appendingPathComponent(
            "document-two.txt")
        record("change-document")
    }

    @objc func showPanel() {
        let window = NSPanel(
            contentRect: NSRect(x: 200, y: 360, width: 300, height: 160), styleMask: [.titled, .closable],
            backing: .buffered, defer: false)
        window.title = "Export dialog"
        window.identifier = NSUserInterfaceItemIdentifier("Export dialog")
        window.setAccessibilitySubrole(.dialog)
        window.isReleasedWhenClosed = false
        // This modeless panel remains visible while testing background input.
        window.hidesOnDeactivate = false
        let text = NSTextField(labelWithString: "Export options")
        text.frame = NSRect(x: 20, y: 65, width: 230, height: 24)
        window.contentView!.addSubview(text)
        let input = NSTextField(string: "Export name")
        input.frame = NSRect(x: 20, y: 110, width: 230, height: 24)
        input.setAccessibilityIdentifier("export-input")
        input.cell?.sendsActionOnEndEditing = false
        input.delegate = self
        input.target = self
        input.action = #selector(submit(_:))
        window.contentView!.addSubview(input)
        let button = NSButton(title: "Dismiss Export", target: self, action: #selector(dismissPanel))
        button.frame = NSRect(x: 20, y: 20, width: 150, height: 28)
        window.contentView!.addSubview(button)
        panel = window
        window.orderBack(nil)
        window.makeKey()
        window.makeFirstResponder(input)
        record("show-dialog")
    }

    @objc func dismissPanel() {
        panel?.close()
        panel = nil
        first.makeKey()
        record("dismiss-dialog")
    }
}

let application = NSApplication.shared
application.setActivationPolicy(.regular)
let delegate = WindowProbe()
application.delegate = delegate
application.run()
