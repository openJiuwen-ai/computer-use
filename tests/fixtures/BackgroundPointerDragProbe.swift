import AppKit

@MainActor final class BackgroundDragPanel: NSPanel {
    override var canBecomeKey: Bool { false }
    override var canBecomeMain: Bool { false }
}

/// A real mouse control which reads the system pointer independently of the
/// event location. The popup level exercises pointer preparation without an
/// application-name rule. The optional external warp is controlled test setup.
@MainActor final class PhysicalDragView: NSView {
    var record: (() -> Void)?
    var interfere = false, movedExternally = false, cleanupProcessed = false, cleanupRestored = false
    var down = 0, dragged = 0, up = 0, value = 0.0, maxPhysicalError = 0.0
    var originalPointer = CGEvent(source: nil)?.location ?? .zero
    var externalPoint: CGPoint?
    var events: [[String: Any]] = []
    override var acceptsFirstResponder: Bool { false }
    override func acceptsFirstMouse(for event: NSEvent?) -> Bool { true }
    override func isAccessibilityElement() -> Bool { true }
    override func accessibilityRole() -> NSAccessibility.Role? { .slider }
    override func accessibilityLabel() -> String? { "Physical pointer position" }
    override func accessibilityValue() -> Any? { NSNumber(value: value) }
    func receive(_ event: NSEvent, kind: String) {
        guard let window else { return }
        let physical = convert(window.mouseLocationOutsideOfEventStream, from: nil)
        let eventPoint = convert(event.locationInWindow, from: nil)
        if kind != "up" { value = max(0, min(100, (physical.x - 30) / 320 * 100)) }
        let error = hypot(physical.x - eventPoint.x, physical.y - eventPoint.y)
        if !movedExternally { maxPhysicalError = max(maxPhysicalError, error) }
        events.append([
            "kind": kind, "physicalX": physical.x, "physicalY": physical.y, "eventX": eventPoint.x,
            "eventY": eventPoint.y, "physicalError": error, "time": ProcessInfo.processInfo.systemUptime
        ])
        if kind == "dragged", interfere, !movedExternally {
            let size = NSScreen.screens.first?.frame.size ?? NSSize(width: 1000, height: 700)
            var point = CGPoint(
                x: min(size.width - 20, max(20, originalPointer.x + 120)),
                y: min(size.height - 20, max(40, originalPointer.y + 120)))
            if hypot(point.x - originalPointer.x, point.y - originalPointer.y) < 10 { point = CGPoint(x: 20, y: 40) }
            if CGWarpMouseCursorPosition(point) == .success {
                externalPoint = point
                movedExternally = true
            }
        }
        needsDisplay = true
        record?()
    }
    override func mouseDown(with event: NSEvent) {
        down += 1
        receive(event, kind: "down")
    }
    override func mouseDragged(with event: NSEvent) {
        dragged += 1
        receive(event, kind: "dragged")
    }
    override func mouseUp(with event: NSEvent) {
        up += 1
        receive(event, kind: "up")
    }
    func cleanupControlledPointerMove() {
        guard !cleanupProcessed else { return }
        cleanupProcessed = true
        if let externalPoint, let current = CGEvent(source: nil)?.location,
            hypot(current.x - externalPoint.x, current.y - externalPoint.y) < 2
        {
            cleanupRestored = CGWarpMouseCursorPosition(originalPointer) == .success
        }
        record?()
    }
    override func draw(_ dirtyRect: NSRect) {
        NSColor.windowBackgroundColor.setFill()
        bounds.fill()
        ("Drag the blue point" as NSString).draw(
            at: NSPoint(x: 30, y: 120), withAttributes: [.foregroundColor: NSColor.labelColor])
        NSColor.systemGray.setFill()
        NSRect(x: 30, y: 68, width: 320, height: 4).fill()
        NSColor.systemBlue.setFill()
        NSBezierPath(ovalIn: NSRect(x: 24 + value / 100 * 320, y: 64, width: 12, height: 12)).fill()
        ("Position: \(Int(value.rounded()))" as NSString).draw(
            at: NSPoint(x: 30, y: 25), withAttributes: [.foregroundColor: NSColor.labelColor])
    }
}

@MainActor final class BackgroundDragDelegate: NSObject, NSApplicationDelegate {
    let directory = Bundle.main.bundleURL.deletingLastPathComponent()
    let view = PhysicalDragView(frame: NSRect(x: 0, y: 0, width: 380, height: 160))
    var panel: NSPanel!
    var foregroundPIDs: [pid_t] = []
    func writeState() {
        guard let panel else { return }
        let pointer = CGEvent(source: nil)?.location ?? .zero
        let foregroundPID = NSWorkspace.shared.frontmostApplication?.processIdentifier ?? 0
        if foregroundPIDs.last != foregroundPID { foregroundPIDs.append(foregroundPID) }
        let data: [String: Any] = [
            "down": view.down, "dragged": view.dragged, "up": view.up, "value": view.value,
            "maxPhysicalError": view.maxPhysicalError, "events": view.events, "movedExternally": view.movedExternally,
            "cleanupProcessed": view.cleanupProcessed, "cleanupRestored": view.cleanupRestored,
            "externalPoint": view.externalPoint.map { ["x": $0.x, "y": $0.y] } ?? [:],
            "originalPointer": ["x": view.originalPointer.x, "y": view.originalPointer.y],
            "pointer": ["x": pointer.x, "y": pointer.y], "foregroundPID": foregroundPID,
            "foregroundPIDs": foregroundPIDs, "window": panel.windowNumber, "active": NSApp.isActive,
            "combinedLeftDown": CGEventSource.buttonState(.combinedSessionState, button: .left),
            "hidLeftDown": CGEventSource.buttonState(.hidSystemState, button: .left)
        ]
        if let bytes = try? JSONSerialization.data(withJSONObject: data) {
            try? bytes.write(to: directory.appendingPathComponent("state.json"), options: .atomic)
        }
    }
    func applicationDidFinishLaunching(_ notification: Notification) {
        try! String(getpid()).write(
            to: directory.appendingPathComponent("fixture.pid"), atomically: true, encoding: .utf8)
        view.interfere = FileManager.default.fileExists(atPath: directory.appendingPathComponent("external-move").path)
        panel = BackgroundDragPanel(
            contentRect: NSRect(x: 400, y: 400, width: 380, height: 160), styleMask: [.borderless, .nonactivatingPanel],
            backing: .buffered, defer: false)
        panel.title = "Background drag surface"
        panel.hidesOnDeactivate = false
        panel.isReleasedWhenClosed = false
        panel.level = .popUpMenu
        panel.contentView = view
        view.record = { [weak self] in self?.writeState() }
        panel.orderFrontRegardless()
        writeState()
        Timer.scheduledTimer(withTimeInterval: 0.02, repeats: true) { _ in
            MainActor.assumeIsolated {
                if FileManager.default.fileExists(atPath: self.directory.appendingPathComponent("restore-pointer").path)
                {
                    self.view.cleanupControlledPointerMove()
                }
                self.writeState()
            }
        }
    }
    func applicationWillTerminate(_ notification: Notification) { view.cleanupControlledPointerMove() }
}

let app = NSApplication.shared
app.setActivationPolicy(.accessory)
let delegate = BackgroundDragDelegate()
app.delegate = delegate
app.run()
