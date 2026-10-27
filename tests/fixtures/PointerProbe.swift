import AppKit

@MainActor final class PointerApplication: NSApplication {
    static let eventsFile = Bundle.main.bundleURL.deletingLastPathComponent().appendingPathComponent(
        "pointer-events.jsonl")
    override func sendEvent(_ event: NSEvent) {
        if [
            .leftMouseDown, .leftMouseUp, .rightMouseDown, .rightMouseUp, .otherMouseDown, .otherMouseUp, .mouseMoved,
            .keyDown, .keyUp, .flagsChanged
        ].contains(event.type) {
            let item: [String: Any] = [
                "type": event.type.rawValue, "window": event.windowNumber, "x": event.locationInWindow.x,
                "y": event.locationInWindow.y, "active": isActive,
                "front": NSWorkspace.shared.frontmostApplication?.processIdentifier ?? 0,
                "code": [.keyDown, .keyUp, .flagsChanged].contains(event.type) ? event.keyCode : 0,
                "flags": event.modifierFlags.rawValue, "keyWindow": keyWindow?.windowNumber ?? 0,
                "foundWindow": event.window != nil
            ]
            if let data = try? JSONSerialization.data(withJSONObject: item, options: [.sortedKeys]),
                let output = FileHandle(forWritingAtPath: Self.eventsFile.path)
            {
                defer { try? output.close() }
                _ = try? output.seekToEnd()
                try? output.write(contentsOf: data + Data([10]))
            }
        }
        super.sendEvent(event)
    }
}

@MainActor final class Canvas: NSView {
    let status: NSTextField
    var downs = 0, ups = 0, text = ""
    init(frame: NSRect, status: NSTextField) {
        self.status = status
        super.init(frame: frame)
        setAccessibilityElement(false)  // No AXPress or actionable AX children.
    }
    required init?(coder: NSCoder) { fatalError() }
    override var acceptsFirstResponder: Bool { true }
    override func draw(_ dirtyRect: NSRect) {
        NSColor.systemGreen.withAlphaComponent(0.3).setFill()
        bounds.fill()
        ("Mouse canvas (no AX action)" as NSString).draw(
            at: NSPoint(x: 30, y: 70), withAttributes: [.font: NSFont.systemFont(ofSize: 20)])
    }
    func record(_ event: NSEvent, down: Bool) {
        if down {
            downs += 1
            window?.makeFirstResponder(self)
        } else {
            ups += 1
        }
        status.stringValue = "button=\(event.buttonNumber) count=\(event.clickCount) down=\(downs) up=\(ups)"
    }
    override func mouseDown(with event: NSEvent) { record(event, down: true) }
    override func mouseUp(with event: NSEvent) { record(event, down: false) }
    override func rightMouseDown(with event: NSEvent) { record(event, down: true) }
    override func rightMouseUp(with event: NSEvent) { record(event, down: false) }
    override func otherMouseDown(with event: NSEvent) { record(event, down: true) }
    override func otherMouseUp(with event: NSEvent) { record(event, down: false) }
    override func keyDown(with event: NSEvent) {
        text += event.characters ?? ""
        status.stringValue = "Typed: " + text
    }
}

@MainActor final class PointerDelegate: NSObject, NSApplicationDelegate {
    var window: NSWindow!
    var cover: NSPanel?
    var nativeClicks = 0
    func applicationDidFinishLaunching(_ notification: Notification) {
        FileManager.default.createFile(atPath: PointerApplication.eventsFile.path, contents: nil)
        let menu = NSMenu(), application = NSMenuItem(), commands = NSMenu()
        commands.addItem(
            withTitle: "Quit Pointer Probe", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        for (title, action, key) in [
            ("Move window", #selector(move), "m"), ("Toggle cover", #selector(toggleCover), "e")
        ] {
            let item = commands.addItem(withTitle: title, action: action, keyEquivalent: key)
            item.target = self
        }
        application.submenu = commands
        menu.addItem(application)
        NSApp.mainMenu = menu
        let edit = NSMenuItem(title: "Edit", action: nil, keyEquivalent: "")
        let editing = NSMenu(title: "Edit")
        editing.addItem(withTitle: "Select All", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")
        // Regression: exclusive input must work even when AX menu enumeration
        // exceeds the backend's shared-desktop compatibility search budget.
        for index in 0..<510 { editing.addItem(withTitle: "Unused command \(index)", action: nil, keyEquivalent: "") }
        edit.submenu = editing
        menu.addItem(edit)
        window = NSWindow(
            contentRect: NSRect(x: 180, y: 200, width: 460, height: 260), styleMask: [.titled, .closable],
            backing: .buffered, defer: false)
        window.title = "Computer Use Pointer Probe"
        window.isReleasedWhenClosed = false
        let status = NSTextField(labelWithString: "Ready")
        status.frame = NSRect(x: 20, y: 20, width: 420, height: 28)
        window.contentView?.addSubview(status)
        let button = NSButton(title: "Native probe", target: self, action: #selector(nativeClick(_:)))
        button.frame = NSRect(x: 300, y: 12, width: 140, height: 28)
        button.setAccessibilityIdentifier("NativeProbe")
        window.contentView?.addSubview(button)
        window.contentView?.addSubview(Canvas(frame: NSRect(x: 20, y: 70, width: 420, height: 170), status: status))
        let field = NSTextField(string: "")
        field.frame = NSRect(x: 20, y: 235, width: 420, height: 24)
        field.setAccessibilityLabel("Selection probe")
        window.contentView?.addSubview(field)
        window.orderBack(nil)
        window.makeKey()
        window.makeMain()
    }
    @objc func move() { window.setFrameOrigin(NSPoint(x: window.frame.minX + 40, y: window.frame.minY + 24)) }
    @objc func nativeClick(_ sender: NSButton) {
        nativeClicks += 1
        sender.title = "Native clicks: \(nativeClicks)"
    }
    @objc func toggleCover() {
        if let cover {
            cover.close()
            self.cover = nil
            return
        }
        let frame = NSRect(x: window.frame.minX + 20, y: window.frame.minY + 70, width: 420, height: 170)
        let panel = NSPanel(
            contentRect: frame, styleMask: [.borderless, .nonactivatingPanel], backing: .buffered, defer: false)
        panel.level = .floating
        panel.backgroundColor = .systemRed
        panel.isReleasedWhenClosed = false
        panel.orderFrontRegardless()
        cover = panel
    }
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }
}

let app = PointerApplication.shared
app.setActivationPolicy(.regular)
let delegate = PointerDelegate()
app.delegate = delegate
app.run()
