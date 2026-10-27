import AppKit

let directory = Bundle.main.bundleURL.deletingLastPathComponent()

@MainActor final class ProbeApplication: NSApplication {
    override func sendEvent(_ event: NSEvent) {
        if [.leftMouseDown, .leftMouseUp, .leftMouseDragged, .keyDown, .keyUp].contains(event.type) {
            let item: [String: Any] = [
                "type": event.type.rawValue, "window": event.windowNumber,
                "point": [event.locationInWindow.x, event.locationInWindow.y], "key": event.keyCode,
                "found": event.window != nil
            ]
            let path = directory.appendingPathComponent("events.jsonl").path
            if !FileManager.default.fileExists(atPath: path) {
                FileManager.default.createFile(atPath: path, contents: nil)
            }
            if let handle = FileHandle(forWritingAtPath: path) {
                _ = try? handle.seekToEnd()
                try? handle.write(contentsOf: JSONSerialization.data(withJSONObject: item) + Data([10]))
                try? handle.close()
            }
        }
        super.sendEvent(event)
    }
}

@MainActor final class DragArea: NSView {
    var downs = 0, ups = 0, drags = 0
    override var acceptsFirstResponder: Bool { true }
    override func acceptsFirstMouse(for event: NSEvent?) -> Bool { true }
    override func draw(_ dirtyRect: NSRect) {
        NSColor(calibratedRed: 0.9, green: 0.95, blue: 0.94, alpha: 1).setFill()
        bounds.fill()
        ("Drag test area" as NSString).draw(
            at: NSPoint(x: 16, y: 16), withAttributes: [.font: NSFont.systemFont(ofSize: 18)])
    }
    override func mouseDown(with event: NSEvent) { downs += 1 }
    override func mouseDragged(with event: NSEvent) { drags += 1 }
    override func mouseUp(with event: NSEvent) { ups += 1 }
}

@MainActor final class AgentPointerProbe: NSObject, NSApplicationDelegate {
    let first = NSTextField(string: ""), second = NSTextField(string: "")
    let canvas = DragArea(frame: NSRect(x: 30, y: 30, width: 580, height: 180))
    var window: NSWindow!, count = 0, timer: Timer?
    func applicationDidFinishLaunching(_ notification: Notification) {
        let menu = NSMenu(), application = NSMenuItem(),
            edit = NSMenuItem(title: "Edit", action: nil, keyEquivalent: "")
        application.submenu = NSMenu(title: "Agent Pointer Probe")
        menu.addItem(application)
        let submenu = NSMenu(title: "Edit")
        submenu.addItem(withTitle: "Paste", action: #selector(NSText.paste(_:)), keyEquivalent: "v")
        submenu.addItem(withTitle: "Select All", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")
        edit.submenu = submenu
        menu.addItem(edit)
        NSApp.mainMenu = menu
        window = NSWindow(
            contentRect: NSRect(x: 90, y: 260, width: 640, height: 380), styleMask: [.titled, .closable],
            backing: .buffered, defer: false)
        window.title = "Agent Pointer Probe"
        window.isReleasedWhenClosed = false
        // Keep this disposable visual probe visible while the user works in
        // ordinary windows; occlusion legitimately hides the agent marker.
        // This changes only the test fixture, never the backend's window level.
        window.level = .floating
        window.appearance = NSAppearance(named: .aqua)
        window.backgroundColor = .white
        for (field, title, x) in [(first, "First input", 30.0), (second, "Second input", 340.0)] {
            field.frame = NSRect(x: x, y: 285, width: 270, height: 32)
            field.setAccessibilityLabel(title)
            let label = NSTextField(labelWithString: title)
            label.frame = NSRect(x: x, y: 328, width: 270, height: 25)
            window.contentView!.addSubview(label)
            window.contentView!.addSubview(field)
        }
        let button = NSButton(title: "Increment", target: self, action: #selector(increment))
        button.frame = NSRect(x: 30, y: 230, width: 140, height: 32)
        window.contentView!.addSubview(button)
        window.contentView!.addSubview(canvas)
        window.orderFrontRegardless()
        timer = Timer.scheduledTimer(withTimeInterval: 0.025, repeats: true) { [weak self] _ in
            MainActor.assumeIsolated { self?.record() }
        }
        record()
    }
    @objc func increment() {
        count += 1
        record()
    }
    func record() {
        let point = CGEvent(source: nil)!.location
        var state: [String: Any] = [
            "pid": getpid(), "window": window.windowNumber, "first": first.stringValue, "second": second.stringValue,
            "count": count, "down": canvas.downs, "up": canvas.ups, "drag": canvas.drags,
            "front": NSWorkspace.shared.frontmostApplication?.processIdentifier ?? 0, "mouse": [point.x, point.y],
            "isKey": window.isKeyWindow,
            "frame": [
                window.frame.minX, CGDisplayBounds(CGMainDisplayID()).height - window.frame.maxY, window.frame.width,
                window.frame.height
            ]
        ]
        if CommandLine.arguments.contains("--observe-pointer") {
            let windows =
                CGWindowListCopyWindowInfo([.optionOnScreenOnly, .excludeDesktopElements], kCGNullWindowID)
                as? [[String: Any]] ?? []
            state["pointers"] = windows.filter {
                ($0[kCGWindowName as String] as? String) == "Computer Use Agent Pointer · v1"
            }.map { item in
                [
                    "id": item[kCGWindowNumber as String] as Any, "pid": item[kCGWindowOwnerPID as String] as Any,
                    "bounds": item[kCGWindowBounds as String] as Any, "alpha": item[kCGWindowAlpha as String] as Any
                ]
            }
            state["locked"] =
                (CGSessionCopyCurrentDictionary() as? [String: Any])?["CGSSessionScreenIsLocked"] as? Bool ?? false
        }
        try? JSONSerialization.data(withJSONObject: state, options: [.sortedKeys]).write(
            to: directory.appendingPathComponent("fixture.json"), options: .atomic)
    }
}
let app = ProbeApplication.shared
app.setActivationPolicy(.regular)
let delegate = AgentPointerProbe()
app.delegate = delegate
app.run()
