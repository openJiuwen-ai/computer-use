import AppKit

/// Passive native layout/text members. Normal responder forwarding sends mouse
/// events to their owning control; there is no accessibility press action.
@MainActor final class PointerDecoration: NSView {
    init(frame: NSRect, role: NSAccessibility.Role, label: String) {
        super.init(frame: frame)
        setAccessibilityElement(true)
        setAccessibilityRole(role)
        setAccessibilityLabel(label)
    }
    required init?(coder: NSCoder) { fatalError() }
    override func acceptsFirstMouse(for event: NSEvent?) -> Bool { true }
    override func draw(_ rect: NSRect) {
        if accessibilityRole() == .staticText {
            ("Button label" as NSString).draw(
                at: NSPoint(x: 15, y: 18), withAttributes: [.font: NSFont.systemFont(ofSize: 17)])
        }
    }
}

/// Disposable canvas. --elements exposes an AXGroup with no primary action;
/// default mode remains visual-only. State records actual view callbacks, not
/// event-post success. Two independent windows detect accidental redirection.
@MainActor final class PointerCanvas: NSView {
    let name: String
    let exposesElements: Bool
    var downs = [Int](), ups = [Int](), clickCounts = [Int](), drags = 0
    var childClicks = 0
    var scrollX = 0.0, scrollY = 0.0
    var marker = NSPoint(x: 80, y: 100)
    var changed: () -> Void = {}
    init(_ name: String, exposesElements: Bool = false, parts: Bool = false) {
        self.name = name
        self.exposesElements = exposesElements
        super.init(frame: NSRect(x: 0, y: 0, width: 400, height: 240))
        setAccessibilityElement(exposesElements)
        if exposesElements {
            setAccessibilityRole(parts ? .button : .group)
            setAccessibilityLabel("Pointer canvas")
            setAccessibilityIdentifier("pointer-canvas")
        }
        if parts {
            let layout = PointerDecoration(frame: NSRect(x: 80, y: 80, width: 240, height: 80), role: .group, label: "")
            let label = PointerDecoration(
                frame: NSRect(x: 40, y: 12, width: 160, height: 56), role: .staticText, label: "Button label")
            layout.addSubview(label)
            addSubview(layout)
        }
    }
    required init?(coder: NSCoder) { fatalError() }
    override var acceptsFirstResponder: Bool { true }
    override func isAccessibilitySelectorAllowed(_ selector: Selector) -> Bool {
        selector == NSSelectorFromString("accessibilityPerformPress")
            ? false : super.isAccessibilitySelectorAllowed(selector)
    }
    override func acceptsFirstMouse(for event: NSEvent?) -> Bool {
        exposesElements || super.acceptsFirstMouse(for: event)
    }
    func addIndependentChild() {
        let button = NSButton(title: "Independent child", target: self, action: #selector(childPressed))
        button.frame = NSRect(x: bounds.midX - 80, y: bounds.midY - 18, width: 160, height: 36)
        button.setAccessibilityIdentifier("pointer-independent-child")
        addSubview(button)
        changed()
    }
    @objc private func childPressed() {
        childClicks += 1
        changed()
    }
    override func draw(_ rect: NSRect) {
        NSColor.systemGreen.withAlphaComponent(0.25).setFill()
        bounds.fill()
        NSColor.systemBlue.setFill()
        NSBezierPath(ovalIn: NSRect(x: marker.x - 15, y: marker.y - 15, width: 30, height: 30)).fill()
        (name as NSString).draw(at: NSPoint(x: 18, y: 210), withAttributes: [.font: NSFont.systemFont(ofSize: 18)])
    }
    func down(_ event: NSEvent) {
        downs.append(event.buttonNumber)
        clickCounts.append(event.clickCount)
        changed()
    }
    func up(_ event: NSEvent) {
        ups.append(event.buttonNumber)
        changed()
    }
    override func mouseDown(with event: NSEvent) { down(event) }
    override func mouseUp(with event: NSEvent) { up(event) }
    override func rightMouseDown(with event: NSEvent) { down(event) }
    override func rightMouseUp(with event: NSEvent) { up(event) }
    override func otherMouseDown(with event: NSEvent) { down(event) }
    override func otherMouseUp(with event: NSEvent) { up(event) }
    override func mouseDragged(with event: NSEvent) {
        drags += 1
        marker = convert(event.locationInWindow, from: nil)
        needsDisplay = true
        changed()
    }
    override func scrollWheel(with event: NSEvent) {
        scrollX += event.scrollingDeltaX
        scrollY += event.scrollingDeltaY
        changed()
    }
    func state() -> [String: Any] {
        let rect = window!.convertToScreen(convert(bounds, to: nil))
        return [
            "downs": downs, "ups": ups, "clickCounts": clickCounts, "drags": drags, "scrollX": scrollX,
            "scrollY": scrollY, "marker": [marker.x, marker.y], "childClicks": childClicks,
            "canvas": [rect.minX, NSScreen.screens[0].frame.maxY - rect.maxY, rect.width, rect.height]
        ]
    }
}

@MainActor final class PointerProbeDelegate: NSObject, NSApplicationDelegate {
    let directory = Bundle.main.bundleURL.deletingLastPathComponent()
    var windows = [NSWindow](), canvases = [PointerCanvas](), moved = false, childAdded = false
    func writeState() {
        guard canvases.count == 2 else { return }
        let value: [String: Any] = [
            "target": canvases[0].state(), "other": canvases[1].state(), "moved": moved, "childAdded": childAdded,
            "keyWindow": NSApp.keyWindow?.title ?? "none", "mainWindow": NSApp.mainWindow?.title ?? "none",
            "foreground": NSWorkspace.shared.frontmostApplication?.processIdentifier ?? 0
        ]
        if let data = try? JSONSerialization.data(withJSONObject: value, options: [.sortedKeys]) {
            try? data.write(to: directory.appendingPathComponent("state.json"), options: .atomic)
        }
    }
    func applicationDidFinishLaunching(_ notification: Notification) {
        for (index, name) in ["V5 pointer target", "V5 pointer other"].enumerated() {
            let window = NSWindow(
                contentRect: NSRect(x: 160 + index * 430, y: 200, width: 400, height: 240),
                styleMask: [.titled, .closable], backing: .buffered, defer: false)
            let parts = CommandLine.arguments.contains("--parts")
            let canvas = PointerCanvas(
                name, exposesElements: parts || CommandLine.arguments.contains("--elements"), parts: parts)
            canvas.changed = { [weak self] in self?.writeState() }
            window.contentView = canvas
            window.title = name
            window.isReleasedWhenClosed = false
            window.orderBack(nil)
            windows.append(window)
            canvases.append(canvas)
        }
        // Establish the other document as the app's prior responder. Selecting
        // the target image must not accidentally deliver to this document.
        windows[1].makeMain()
        windows[1].makeKey()
        if CommandLine.arguments.contains("--foreground") {
            windows[0].makeKeyAndOrderFront(nil)
            NSApp.activate(ignoringOtherApps: true)
        }
        writeState()
        _ = Timer.scheduledTimer(withTimeInterval: 0.05, repeats: true) { _ in
            MainActor.assumeIsolated {
                if !self.moved,
                    FileManager.default.fileExists(atPath: self.directory.appendingPathComponent("move-window").path)
                {
                    self.windows[0].setFrameOrigin(
                        NSPoint(x: self.windows[0].frame.minX + 40, y: self.windows[0].frame.minY + 25))
                    self.moved = true
                }
                if !self.childAdded, self.canvases[0].exposesElements,
                    FileManager.default.fileExists(atPath: self.directory.appendingPathComponent("add-child").path)
                {
                    self.childAdded = true
                    self.canvases[0].addIndependentChild()
                }
                self.writeState()
            }
        }
    }
}

let application = NSApplication.shared
application.setActivationPolicy(.regular)
let delegate = PointerProbeDelegate()
application.delegate = delegate
application.run()
