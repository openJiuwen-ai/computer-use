import AppKit

/// First-mouse rejection, overlapping documents and a non-key popup share the
/// ordinary AppKit dispatch path. The oracle records real received events.
@MainActor final class SyntheticCanvas: NSView {
    var onEvent: ((NSEvent, String) -> Void)?
    override var acceptsFirstResponder: Bool { true }
    override func isAccessibilityElement() -> Bool { true }
    override func accessibilityRole() -> NSAccessibility.Role? { .group }
    override func accessibilityLabel() -> String? { "Mouse canvas" }
    override func accessibilityIdentifier() -> String { "mouse-canvas" }
    override func acceptsFirstMouse(for event: NSEvent?) -> Bool { false }
    override func mouseDown(with event: NSEvent) { onEvent?(event, "down") }
    override func mouseUp(with event: NSEvent) { onEvent?(event, "up") }
    override func rightMouseDown(with event: NSEvent) { onEvent?(event, "right-down") }
    override func rightMouseUp(with event: NSEvent) { onEvent?(event, "right-up") }
    override func mouseDragged(with event: NSEvent) { onEvent?(event, "drag") }
    override func draw(_ rect: NSRect) {
        NSColor.systemGreen.withAlphaComponent(0.2).setFill()
        bounds.fill()
        ("Mouse canvas — first mouse refused" as NSString).draw(at: NSPoint(x: 12, y: 50))
    }
}

@MainActor final class SyntheticPanel: NSPanel {
    override var canBecomeKey: Bool { false }
    override var canBecomeMain: Bool { false }
}

@MainActor final class SyntheticApplication: NSApplication {
    override func sendEvent(_ event: NSEvent) {
        (delegate as? SyntheticDelegate)?.record(event: "application-event", native: event)
        super.sendEvent(event)
    }
}

@MainActor final class SyntheticDelegate: NSObject, NSApplicationDelegate {
    let root = Bundle.main.bundleURL.deletingLastPathComponent()
    var windows: [NSWindow] = []
    var canvas: SyntheticCanvas!
    var popup: SyntheticPanel?
    var downs = 0, ups = 0, rightDowns = 0, drags = 0, clicks: [Int] = []
    var semantic = 0, popupClicks = 0
    let input = NSTextField(string: "before")
    let otherInput = NSTextField(string: "untouched")

    func record(event: String = "sample", native: NSEvent? = nil) {
        guard windows.count == 2 else { return }
        let data: [String: Any] = [
            "event": event, "type": native?.type.rawValue as Any? ?? NSNull(),
            "subtype": native.map { $0.type.rawValue == 21 || $0.type == .appKitDefined ? Int($0.subtype.rawValue) : 0 }
                as Any? ?? NSNull(), "eventWindow": native?.windowNumber as Any? ?? NSNull(),
            "flags": native?.modifierFlags.rawValue as Any? ?? NSNull(), "active": NSApp.isActive,
            "keyWindow": NSApp.keyWindow?.title ?? "", "pointer": [NSEvent.mouseLocation.x, NSEvent.mouseLocation.y],
            "foreground": NSWorkspace.shared.frontmostApplication?.processIdentifier ?? 0, "downs": downs, "ups": ups,
            "rightDowns": rightDowns, "drags": drags, "clicks": clicks, "input": input.stringValue,
            "otherInput": otherInput.stringValue, "semantic": semantic, "popupClicks": popupClicks,
            "popupVisible": popup?.isVisible ?? false, "targetWindow": windows[0].windowNumber,
            "otherWindow": windows[1].windowNumber
        ]
        guard let bytes = try? JSONSerialization.data(withJSONObject: data, options: [.sortedKeys]) else { return }
        try? bytes.write(to: root.appendingPathComponent("state.json"), options: .atomic)
        if event != "sample" {
            let file = root.appendingPathComponent("events.jsonl")
            if !FileManager.default.fileExists(atPath: file.path) {
                FileManager.default.createFile(atPath: file.path, contents: nil)
            }
            if let handle = try? FileHandle(forWritingTo: file) {
                _ = try? handle.seekToEnd()
                try? handle.write(contentsOf: bytes + Data([10]))
                try? handle.close()
            }
        }
    }

    @objc func pressSemantic() {
        semantic += 1
        record(event: "semantic")
    }
    @objc func showPopup() {
        let panel = SyntheticPanel(
            contentRect: NSRect(x: 170, y: 250, width: 240, height: 110), styleMask: [.borderless, .nonactivatingPanel],
            backing: .buffered, defer: false)
        panel.title = "Synthetic popup"
        panel.hidesOnDeactivate = false
        panel.isReleasedWhenClosed = false
        panel.level = .popUpMenu
        let choice = NSButton(title: "Popup choice", target: self, action: #selector(choosePopup))
        choice.frame = NSRect(x: 15, y: 30, width: 200, height: 40)
        panel.contentView?.addSubview(choice)
        windows[0].addChildWindow(panel, ordered: .above)
        popup = panel
        panel.orderFrontRegardless()
        record(event: "popup-shown")
    }
    @objc func choosePopup() {
        popupClicks += 1
        popup?.orderOut(nil)
        record(event: "popup-choice")
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        try! String(getpid()).write(to: root.appendingPathComponent("fixture.pid"), atomically: true, encoding: .utf8)
        for (index, name) in ["Synthetic target", "Synthetic other"].enumerated() {
            let window = NSWindow(
                contentRect: NSRect(x: 140 + index * 60, y: 170 + index * 30, width: 460, height: 320),
                styleMask: [.titled, .closable], backing: .buffered, defer: false)
            window.title = name
            window.tabbingMode = .disallowed
            window.isReleasedWhenClosed = false
            window.orderBack(nil)
            windows.append(window)
        }
        canvas = SyntheticCanvas(frame: NSRect(x: 20, y: 125, width: 420, height: 150))
        canvas.onEvent = { [weak self] event, kind in
            guard let self else { return }
            if kind == "down" {
                downs += 1
                clicks.append(event.clickCount)
            }
            if kind == "up" { ups += 1 }
            if kind == "right-down" { rightDowns += 1 }
            if kind == "drag" { drags += 1 }
            record(event: "canvas-" + kind, native: event)
        }
        windows[0].contentView?.addSubview(canvas)
        input.frame = NSRect(x: 20, y: 75, width: 420, height: 28)
        input.setAccessibilityIdentifier("target-input")
        windows[0].contentView?.addSubview(input)
        otherInput.frame = input.frame
        otherInput.setAccessibilityIdentifier("other-input")
        windows[1].contentView?.addSubview(otherInput)
        let button = NSButton(title: "Semantic action", target: self, action: #selector(pressSemantic))
        button.frame = NSRect(x: 20, y: 25, width: 180, height: 32)
        windows[0].contentView?.addSubview(button)
        let popup = NSButton(title: "Show popup", target: self, action: #selector(showPopup))
        popup.frame = NSRect(x: 220, y: 25, width: 180, height: 32)
        windows[0].contentView?.addSubview(popup)
        windows[1].makeKey()
        windows[1].makeMain()
        windows[1].makeFirstResponder(otherInput)
        record(event: "ready")
        Timer.scheduledTimer(withTimeInterval: 0.03, repeats: true) { _ in MainActor.assumeIsolated { self.record() } }
    }
}

let application = SyntheticApplication.shared
application.setActivationPolicy(.regular)
let delegate = SyntheticDelegate()
application.delegate = delegate
application.run()
