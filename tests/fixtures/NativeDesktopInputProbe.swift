import AppKit
import CoreGraphics

/// A visual-only responder. No editable AX object or AX focus route is exposed.
@MainActor final class DesktopInputCanvas: NSView {
    let name: String
    var text = "", commits = 0, selected = false
    var downs = [Int](), ups = [Int](), counts = [Int](), drags = 0
    var scrollX = 0.0, scrollY = 0.0
    var marker = NSPoint(x: 80, y: 100)
    var changed: () -> Void = {}
    init(_ name: String) {
        self.name = name
        super.init(frame: NSRect(x: 0, y: 0, width: 400, height: 280))
        setAccessibilityElement(false)
        setAccessibilityChildren([])
    }
    required init?(coder: NSCoder) { fatalError() }
    override var acceptsFirstResponder: Bool { true }
    override func acceptsFirstMouse(for event: NSEvent?) -> Bool { true }
    override func draw(_ rect: NSRect) {
        NSColor.systemTeal.withAlphaComponent(0.25).setFill()
        bounds.fill()
        NSColor.systemBlue.setFill()
        NSBezierPath(ovalIn: NSRect(x: marker.x - 15, y: marker.y - 15, width: 30, height: 30)).fill()
        (name + "\nText: " + text + "\nReturn: \(commits)" as NSString).draw(
            in: NSRect(x: 20, y: 180, width: 360, height: 80), withAttributes: [.font: NSFont.systemFont(ofSize: 17)])
    }
    func down(_ event: NSEvent) {
        window?.makeFirstResponder(self)
        downs.append(event.buttonNumber)
        counts.append(event.clickCount)
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
    override func performKeyEquivalent(with event: NSEvent) -> Bool {
        if event.modifierFlags.contains(.command), event.charactersIgnoringModifiers == "a" {
            selected = true
            changed()
            return true
        }
        return false
    }
    override func keyDown(with event: NSEvent) {
        if event.keyCode == 36 {
            commits += 1
        } else if let characters = event.characters {
            if selected {
                text = ""
                selected = false
            }
            text += characters
        }
        needsDisplay = true
        changed()
    }
    func state() -> [String: Any] {
        let canvas = window!.convertToScreen(convert(bounds, to: nil)), frame = window!.frame
        let top = NSScreen.screens[0].frame.maxY
        return [
            "text": text, "commits": commits, "downs": downs, "ups": ups, "counts": counts, "drags": drags,
            "scrollX": scrollX, "scrollY": scrollY, "marker": [marker.x, marker.y],
            "canvas": [canvas.minX, top - canvas.maxY, canvas.width, canvas.height],
            "frame": [frame.minX, top - frame.maxY, frame.width, frame.height]
        ]
    }
}

@MainActor final class DesktopInputDelegate: NSObject, NSApplicationDelegate {
    let directory = Bundle.main.bundleURL.deletingLastPathComponent()
    var windows = [NSWindow](), canvases = [DesktopInputCanvas](), switched = false
    func write() {
        guard canvases.count == 2 else { return }
        let value: [String: Any] = [
            "pid": ProcessInfo.processInfo.processIdentifier, "target": canvases[0].state(),
            "other": canvases[1].state(), "switched": switched, "keyWindow": NSApp.keyWindow?.title ?? "none",
            "foreground": NSWorkspace.shared.frontmostApplication?.processIdentifier ?? 0
        ]
        if let data = try? JSONSerialization.data(withJSONObject: value, options: [.sortedKeys]) {
            try? data.write(to: directory.appendingPathComponent("state.json"), options: .atomic)
        }
    }
    func applicationDidFinishLaunching(_ notification: Notification) {
        for (index, name) in ["Desktop input target", "Desktop input other"].enumerated() {
            let window = NSWindow(
                contentRect: NSRect(x: 160 + index * 430, y: 250, width: 400, height: 280),
                styleMask: [.titled, .closable], backing: .buffered, defer: false)
            let canvas = DesktopInputCanvas(name)
            canvas.changed = { [weak self] in self?.write() }
            window.title = name
            window.contentView = canvas
            window.isReleasedWhenClosed = false
            window.orderFrontRegardless()
            windows.append(window)
            canvases.append(canvas)
        }
        write()
        _ = Timer.scheduledTimer(withTimeInterval: 0.025, repeats: true) { _ in
            MainActor.assumeIsolated {
                if !self.switched,
                    FileManager.default.fileExists(atPath: self.directory.appendingPathComponent("select-other").path)
                {
                    self.windows[1].makeKeyAndOrderFront(nil)
                    self.windows[1].makeFirstResponder(self.canvases[1])
                    self.switched = true
                }
                self.write()
            }
        }
    }
}

let app = NSApplication.shared
app.setActivationPolicy(.regular)
let delegate = DesktopInputDelegate()
app.delegate = delegate
app.run()
