import AppKit

/// Independent native oracle for actual keyboard delivery and menu equivalents.
@MainActor final class ShortcutApplication: NSApplication {
    var received: ((NSEvent) -> Void)?
    override func sendEvent(_ event: NSEvent) {
        received?(event)
        super.sendEvent(event)
    }
}

/// A document command follows the responder chain; an explicit app delegate
/// target would hide failures caused by an inactive/missing key-window context.
@MainActor final class ShortcutDocumentView: NSView {
    var onDocumentCommand: () -> Void = {}
    override var acceptsFirstResponder: Bool { true }
    @objc func countDocumentCommand(_ sender: Any?) { onDocumentCommand() }
}

@MainActor final class ShortcutDelegate: NSObject, NSApplicationDelegate {
    let folder = URL(fileURLWithPath: CommandLine.arguments[1])
    var window: NSWindow!
    var field: NSTextField!
    var guardField: NSTextField!
    var count = 0
    var undoCount = 0
    var documentCount = 0
    var events = [[String: Any]]()

    func record() {
        guard let window, let field, let guardField else { return }
        let value: [String: Any] = [
            "pid": getpid(), "window": window.windowNumber, "active": NSApp.isActive, "key": window.isKeyWindow,
            "frontmostPID": NSWorkspace.shared.frontmostApplication?.processIdentifier ?? 0,
            "keyWindow": NSApp.keyWindow?.windowNumber ?? 0, "mainWindow": NSApp.mainWindow?.windowNumber ?? 0,
            "firstResponder": window.firstResponder.map { String(describing: type(of: $0)) } ?? "none", "count": count,
            "undoCount": undoCount, "documentCount": documentCount, "text": field.stringValue,
            "guard": guardField.stringValue, "events": events
        ]
        try? JSONSerialization.data(withJSONObject: value, options: [.sortedKeys]).write(
            to: folder.appendingPathComponent("state.json"), options: .atomic)
    }

    func receive(_ event: NSEvent) {
        let keyboard = [.keyDown, .keyUp, .flagsChanged].contains(event.type)
        let mouse = [
            .mouseMoved, .leftMouseDown, .leftMouseUp, .rightMouseDown, .rightMouseUp, .otherMouseDown, .otherMouseUp
        ].contains(event.type)
        guard keyboard || mouse || event.type == .appKitDefined || event.type.rawValue == 21 else { return }
        var value: [String: Any] = [
            "type": event.type.rawValue, "timestamp": event.timestamp, "flags": event.modifierFlags.rawValue,
            "window": event.windowNumber, "active": NSApp.isActive, "key": window?.isKeyWindow ?? false
        ]
        if let cg = event.cgEvent { value["sourcePid"] = cg.getIntegerValueField(.eventSourceUnixProcessID) }
        if keyboard { value["keyCode"] = event.keyCode }
        if keyboard && event.type != .flagsChanged {
            value["characters"] = event.characters ?? "<nil>"
            value["charactersIgnoringModifiers"] = event.charactersIgnoringModifiers ?? "<nil>"
        }
        if mouse { value["point"] = [event.locationInWindow.x, event.locationInWindow.y] }
        if event.type == .appKitDefined { value["subtype"] = event.subtype.rawValue }
        events.append(value)
        if events.count > 160 { events.removeFirst(events.count - 160) }
        record()
    }

    @objc func countL() {
        count += 1
        record()
    }
    @objc func countUndo() {
        undoCount += 1
        record()
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        let menu = NSMenu()
        let appItem = NSMenuItem()
        menu.addItem(appItem)
        let commands = NSMenu(title: "Probe")
        for (title, action, key) in [("Count L", #selector(countL), "l"), ("Count Undo", #selector(countUndo), "z")] {
            let item = NSMenuItem(title: title, action: action, keyEquivalent: key)
            item.target = self
            item.keyEquivalentModifierMask = .command
            commands.addItem(item)
        }
        appItem.submenu = commands
        NSApp.mainMenu = menu
        let documentCommand = NSMenuItem(
            title: "Count document command", action: #selector(ShortcutDocumentView.countDocumentCommand(_:)),
            keyEquivalent: "k")
        documentCommand.target = nil
        documentCommand.keyEquivalentModifierMask = .command
        commands.addItem(documentCommand)
        window = NSWindow(
            contentRect: NSRect(x: 180, y: 240, width: 520, height: 220), styleMask: [.titled, .closable],
            backing: .buffered, defer: false)
        window.title = "Keyboard shortcut probe"
        window.isReleasedWhenClosed = false
        window.tabbingMode = .disallowed
        field = NSTextField(frame: NSRect(x: 30, y: 120, width: 450, height: 28))
        field.setAccessibilityLabel("Shortcut input")
        guardField = NSTextField(frame: NSRect(x: 30, y: 65, width: 450, height: 28))
        guardField.stringValue = "GUARD"
        guardField.isEditable = false
        guardField.setAccessibilityLabel("Unrelated guard")
        let document = ShortcutDocumentView(frame: window.contentView!.bounds)
        document.onDocumentCommand = { [weak self] in
            self?.documentCount += 1
            self?.record()
        }
        window.contentView = document
        window.contentView!.addSubview(field)
        window.contentView!.addSubview(guardField)
        (NSApp as? ShortcutApplication)?.received = { [weak self] event in self?.receive(event) }
        window.makeKeyAndOrderFront(nil)
        window.makeFirstResponder(field)
        NSApp.activate(ignoringOtherApps: true)
        record()
        _ = Timer.scheduledTimer(withTimeInterval: 0.05, repeats: true) { _ in
            MainActor.assumeIsolated { self.record() }
        }
    }
}

let application = ShortcutApplication.shared
application.setActivationPolicy(.regular)
let delegate = ShortcutDelegate()
application.delegate = delegate
application.run()
