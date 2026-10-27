import AppKit

@MainActor final class DocumentProbeApplication: NSApplication {
    override func sendEvent(_ event: NSEvent) {
        if [.leftMouseDown, .leftMouseUp, .mouseMoved, .appKitDefined].contains(event.type),
            let delegate = delegate as? DocumentActionDelegate
        {
            delegate.event([
                "type": event.type.rawValue, "window": event.windowNumber,
                "point": [event.locationInWindow.x, event.locationInWindow.y],
                "sourcePid": event.cgEvent?.getIntegerValueField(.eventSourceUnixProcessID) ?? -1,
                "keyWindow": keyWindow?.windowNumber ?? 0, "active": isActive
            ])
        }
        super.sendEvent(event)
    }
}

/// Two ordinary documents expose a nil-target action and a content click.
/// The oracle records AppKit state independently of the driver's AX receipts.
@MainActor final class DocumentResponder: NSView {
    var actions = 0
    var downs = 0
    var record: () -> Void = {}
    override var acceptsFirstResponder: Bool { true }
    override func acceptsFirstMouse(for event: NSEvent?) -> Bool { false }
    @objc func markDocument(_ sender: Any?) {
        actions += 1
        record()
    }
    override func mouseDown(with event: NSEvent) {
        downs += 1
        record()
    }
    override func draw(_ dirtyRect: NSRect) {
        NSColor.textBackgroundColor.setFill()
        bounds.fill()
        "Content target".draw(
            at: NSPoint(x: 20, y: 40),
            withAttributes: [.foregroundColor: NSColor.labelColor, .font: NSFont.systemFont(ofSize: 20)])
    }
}

@MainActor final class DocumentActionDelegate: NSObject, NSApplicationDelegate {
    let folder = URL(fileURLWithPath: CommandLine.arguments[1])
    var windows = [NSWindow]()
    var responders = [DocumentResponder]()
    var generation = 0
    var lastCommand = ""
    func event(_ value: [String: Any]) {
        let file = folder.appendingPathComponent("events.jsonl")
        if !FileManager.default.fileExists(atPath: file.path) {
            FileManager.default.createFile(atPath: file.path, contents: nil)
        }
        if let handle = try? FileHandle(forWritingTo: file) {
            _ = try? handle.seekToEnd()
            try? handle.write(contentsOf: JSONSerialization.data(withJSONObject: value) + Data([10]))
            try? handle.close()
        }
    }
    func record() {
        guard windows.count == 2 else { return }
        let value: [String: Any] = [
            "pid": getpid(), "active": NSApp.isActive,
            "foreground": NSWorkspace.shared.frontmostApplication?.processIdentifier ?? -1,
            "keyWindow": NSApp.keyWindow?.windowNumber ?? 0, "generation": generation,
            "documents": windows.enumerated().map { i, window -> [String: Any] in
                let button = window.contentView!.subviews.first { $0 is NSButton }!
                let point = window.convertPoint(
                    toScreen: button.convert(NSPoint(x: button.bounds.midX, y: button.bounds.midY), to: nil))
                return [
                    "window": window.windowNumber, "title": window.title, "actions": responders[i].actions,
                    "downs": responders[i].downs, "key": window.isKeyWindow, "main": window.isMainWindow,
                    "responder": window.firstResponder === responders[i],
                    "buttonScreen": [point.x, NSScreen.screens[0].frame.maxY - point.y],
                    "frame": [
                        window.frame.minX, NSScreen.screens[0].frame.maxY - window.frame.maxY, window.frame.width,
                        window.frame.height
                    ]
                ]
            }
        ]
        try? JSONSerialization.data(withJSONObject: value, options: [.sortedKeys]).write(
            to: folder.appendingPathComponent("state.json"), options: .atomic)
    }
    func tick() {
        if let data = try? Data(contentsOf: folder.appendingPathComponent("command.json")),
            let command = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
            let id = command["id"] as? String, id != lastCommand
        {
            lastCommand = id
            if command["kind"] as? String == "front-other" {
                windows[1].makeKeyAndOrderFront(nil)
                NSApp.activate(ignoringOtherApps: true)
            } else if command["kind"] as? String == "front-target" {
                windows[0].makeKeyAndOrderFront(nil)
                NSApp.activate(ignoringOtherApps: true)
            } else if command["kind"] as? String == "close-target" {
                windows[0].close()
            }
            generation += 1
        }
        record()
    }
    func applicationDidFinishLaunching(_ notification: Notification) {
        let menu = NSMenu(), app = NSMenuItem()
        app.submenu = NSMenu()
        app.submenu!.addItem(withTitle: "Quit", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        menu.addItem(app)
        NSApp.mainMenu = menu
        for i in 0..<2 {
            let window = NSWindow(
                contentRect: NSRect(x: 80 + 530 * i, y: 180, width: 500, height: 320), styleMask: [.titled, .closable],
                backing: .buffered, defer: false)
            window.title = "Document context \(i)"
            window.tabbingMode = .disallowed
            window.isReleasedWhenClosed = false
            let responder = DocumentResponder(frame: NSRect(x: 20, y: 20, width: 460, height: 180))
            responder.setAccessibilityIdentifier("document-content-\(i)")
            let button = NSButton(
                title: "Document action", target: nil, action: #selector(DocumentResponder.markDocument(_:)))
            button.frame = NSRect(x: 120, y: 240, width: 240, height: 40)
            button.setAccessibilityIdentifier("document-action-\(i)")
            window.contentView!.addSubview(responder)
            window.contentView!.addSubview(button)
            window.makeFirstResponder(responder)
            window.orderBack(nil)
            windows.append(window)
            responders.append(responder)
            responder.record = { [weak self] in self?.record() }
        }
        windows[1].makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
        record()
        _ = Timer.scheduledTimer(withTimeInterval: 0.025, repeats: true) { _ in MainActor.assumeIsolated { self.tick() }
        }
    }
}

let application = DocumentProbeApplication.shared
application.setActivationPolicy(.regular)
let delegate = DocumentActionDelegate()
application.delegate = delegate
application.run()
