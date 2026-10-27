import AppKit

/// Disposable in-process oracle for mouse routing, window state and AX effects.
@MainActor final class MechanismView: NSView {
    var clicks = 0
    var downs = 0
    var pending = false
    var record: ([String: Any]) -> Void = { _ in }
    override var acceptsFirstResponder: Bool { true }
    override func acceptsFirstMouse(for event: NSEvent?) -> Bool { CommandLine.arguments.contains("--first-mouse") }
    override func mouseDown(with event: NSEvent) {
        downs += 1
        pending = true
        let point = convert(event.locationInWindow, from: nil)
        record([
            "stage": "view-down", "point": [point.x, point.y],
            "windowPoint": [event.locationInWindow.x, event.locationInWindow.y],
            "global": event.cgEvent.map { [$0.location.x, $0.location.y] } ?? [], "window": window!.windowNumber
        ])
    }
    override func mouseUp(with event: NSEvent) {
        if pending { clicks += 1 }
        pending = false
        record(["stage": "view-up", "window": window!.windowNumber])
    }
    override func draw(_ rect: NSRect) {
        NSColor.systemGreen.setFill()
        bounds.fill()
        "Mouse target".draw(at: NSPoint(x: 15, y: 25), withAttributes: [.font: NSFont.systemFont(ofSize: 18)])
    }
}

@MainActor final class ErrorToggle: NSButton {
    var presses = 0
    var mode = "error"
    override func accessibilityPerformPress() -> Bool {
        presses += 1
        if mode != "unchanged" { state = state == .on ? .off : .on }
        if mode == "disable" { isEnabled = false }
        if mode == "revert" {
            DispatchQueue.main.asyncAfter(deadline: .now() + .milliseconds(100)) { self.state = .off }
        }
        return mode == "success"
    }
}

@MainActor final class MechanismApplication: NSApplication {
    override func sendEvent(_ event: NSEvent) {
        if [.leftMouseDown, .leftMouseUp, .mouseMoved, .mouseEntered, .appKitDefined].contains(event.type) {
            (delegate as? MechanismDelegate)?.event([
                "stage": "application", "type": event.type.rawValue, "window": event.windowNumber,
                "point": [event.locationInWindow.x, event.locationInWindow.y],
                "keyWindow": keyWindow?.windowNumber ?? 0, "active": isActive,
                "sourcePid": event.cgEvent?.getIntegerValueField(.eventSourceUnixProcessID) ?? -1
            ])
        }
        super.sendEvent(event)
    }
}

@MainActor final class MechanismDelegate: NSObject, NSApplicationDelegate {
    let directory = URL(fileURLWithPath: CommandLine.arguments[1])
    var windows = [NSWindow](), views = [MechanismView](), editors = [NSTextField]()
    var toggle: ErrorToggle!
    var modal: NSWindow?
    var lastCommand = ""
    var generation = 0
    func event(_ value: [String: Any]) {
        let file = directory.appendingPathComponent("events.jsonl")
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
        let height = NSScreen.screens[0].frame.maxY
        let value: [String: Any] = [
            "pid": getpid(), "generation": generation, "active": NSApp.isActive,
            "foreground": NSWorkspace.shared.frontmostApplication?.processIdentifier ?? -1,
            "keyWindow": NSApp.keyWindow?.windowNumber ?? 0, "mainWindow": NSApp.mainWindow?.windowNumber ?? 0,
            "toggle": ["state": toggle.state.rawValue, "presses": toggle.presses, "enabled": toggle.isEnabled],
            "screens": NSScreen.screens.map {
                [
                    "frame": [$0.frame.minX, $0.frame.minY, $0.frame.width, $0.frame.height],
                    "scale": $0.backingScaleFactor
                ]
            },
            "windows": windows.enumerated().map { i, window -> [String: Any] in
                let center = window.convertPoint(toScreen: views[i].convert(NSPoint(x: 100, y: 60), to: nil))
                return [
                    "id": window.windowNumber, "title": window.title, "visible": window.isVisible,
                    "key": window.isKeyWindow, "main": window.isMainWindow, "clicks": views[i].clicks,
                    "downs": views[i].downs, "text": editors[i].stringValue,
                    "responder": window.firstResponder === views[i], "point": [center.x, height - center.y],
                    "viewPoint": [100, 60],
                    "frame": [window.frame.minX, height - window.frame.maxY, window.frame.width, window.frame.height]
                ]
            }
        ]
        try? JSONSerialization.data(withJSONObject: value).write(
            to: directory.appendingPathComponent("state.json"), options: .atomic)
    }
    func tick() {
        if let data = try? Data(contentsOf: directory.appendingPathComponent("command.json")),
            let command = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
            let id = command["id"] as? String, id != lastCommand
        {
            lastCommand = id
            switch command["kind"] as? String {
            case "front-target":
                windows[0].makeKeyAndOrderFront(nil)
                NSApp.activate(ignoringOtherApps: true)
            case "resign-key": NSApp.keyWindow?.resignKey()
            case "front-other":
                windows[1].makeKeyAndOrderFront(nil)
                NSApp.activate(ignoringOtherApps: true)
            case "switch-window": windows[1].makeKeyAndOrderFront(nil)
            case "switch-element": windows[0].makeFirstResponder(editors[0])
            case "close-target": windows[0].close()
            case "sheet":
                let sheet = NSWindow(
                    contentRect: NSRect(x: 0, y: 0, width: 280, height: 160), styleMask: [.titled], backing: .buffered,
                    defer: false)
                sheet.title = "Mechanism modal"
                modal = sheet
                windows[0].beginSheet(sheet)
            default: break
            }
            generation += 1
        }
        record()
    }
    func applicationDidFinishLaunching(_ notification: Notification) {
        let menu = NSMenu(), item = NSMenuItem()
        item.submenu = NSMenu()
        item.submenu!.addItem(withTitle: "Quit", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        menu.addItem(item)
        NSApp.mainMenu = menu
        let screenIndex =
            CommandLine.arguments.firstIndex(of: "--screen").map { Int(CommandLine.arguments[$0 + 1])! } ?? 0
        let screen = NSScreen.screens[min(screenIndex, NSScreen.screens.count - 1)]
        for i in 0..<2 {
            let window = NSWindow(
                contentRect: NSRect(
                    x: screen.visibleFrame.minX + 80 + CGFloat(i) * 530, y: screen.visibleFrame.minY + 160, width: 500,
                    height: 320), styleMask: [.titled, .closable], backing: .buffered, defer: false)
            window.title = "Mechanism document \(i)"
            window.tabbingMode = .disallowed
            window.isReleasedWhenClosed = false
            let view = MechanismView(frame: NSRect(x: 30, y: 40, width: 240, height: 100))
            view.setAccessibilityIdentifier("mechanism-pointer-\(i)")
            view.record = { [weak self] in self?.event($0) }
            let editor = NSTextField(string: "GUARD \(i)")
            editor.frame = NSRect(x: 30, y: 160, width: 350, height: 26)
            editor.setAccessibilityIdentifier("mechanism-editor-\(i)")
            window.contentView!.addSubview(view)
            window.contentView!.addSubview(editor)
            window.makeFirstResponder(view)
            window.orderBack(nil)
            windows.append(window)
            views.append(view)
            editors.append(editor)
        }
        toggle = ErrorToggle(checkboxWithTitle: "Error toggle", target: nil, action: nil)
        toggle.frame = NSRect(x: 30, y: 230, width: 250, height: 28)
        toggle.setAccessibilityIdentifier("mechanism-error-toggle")
        toggle.mode = CommandLine.arguments.firstIndex(of: "--toggle").map { CommandLine.arguments[$0 + 1] } ?? "error"
        windows[0].contentView!.addSubview(toggle)
        record()
        _ = Timer.scheduledTimer(withTimeInterval: 0.02, repeats: true) { _ in MainActor.assumeIsolated { self.tick() }
        }
    }
}

let application = MechanismApplication.shared
application.setActivationPolicy(.regular)
let delegate = MechanismDelegate()
application.delegate = delegate
application.run()
