// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit

/// A concrete, mouse-operated AX editor without a semantic Press action.
@MainActor final class HoverPointerTarget: NSView {
    var value = "target"
    var entered = 0, moved = 0, downs = 0, ups = 0, textWrites = 0
    var tracking: NSTrackingArea?
    var hover: (String, NSEvent) -> Void = { _, _ in }
    var record: () -> Void = {}

    override var acceptsFirstResponder: Bool { true }
    override func acceptsFirstMouse(for event: NSEvent?) -> Bool { true }
    override func updateTrackingAreas() {
        if let tracking { removeTrackingArea(tracking) }
        let area = NSTrackingArea(
            rect: bounds, options: [.mouseEnteredAndExited, .mouseMoved, .activeAlways], owner: self, userInfo: nil)
        tracking = area
        addTrackingArea(area)
        super.updateTrackingAreas()
    }
    override func mouseEntered(with event: NSEvent) {
        entered += 1
        hover("entered", event)
        record()
    }
    override func mouseMoved(with event: NSEvent) {
        moved += 1
        hover("moved", event)
        record()
    }
    override func mouseDown(with event: NSEvent) {
        downs += 1
        record()
    }
    override func mouseUp(with event: NSEvent) {
        ups += 1
        record()
    }
    override func draw(_ dirtyRect: NSRect) {
        NSColor.controlBackgroundColor.setFill()
        bounds.fill()
        value.draw(at: NSPoint(x: 8, y: 8), withAttributes: [.font: NSFont.systemFont(ofSize: 15)])
    }
    override nonisolated func accessibilityRole() -> NSAccessibility.Role? { .textArea }
    override nonisolated func accessibilityValue() -> Any? { MainActor.assumeIsolated { value } }
    override nonisolated func accessibilitySelectedText() -> String? { "" }
    override nonisolated func setAccessibilitySelectedText(_ text: String?) {
        MainActor.assumeIsolated {
            textWrites += 1
            value += text ?? ""
            record()
        }
    }
    override nonisolated func isAccessibilityFocused() -> Bool {
        MainActor.assumeIsolated { window?.firstResponder === self }
    }
    func reset() {
        entered = 0
        moved = 0
        downs = 0
        ups = 0
        textWrites = 0
        value = "target"
    }
}

@MainActor final class HoverPointerDelegate: NSObject, NSApplicationDelegate {
    let folder = Bundle.main.bundleURL.deletingLastPathComponent()
    let initialFrame = NSRect(x: 110, y: 95, width: 160, height: 55)
    let target = HoverPointerTarget()
    let decoy = HoverPointerTarget()
    let other = NSTextField(string: "unchanged")
    var window: NSWindow!
    var mode = "stable", lastCommand = "", changed = false
    var eventPoint: NSPoint?
    var hoverKind = ""

    var fixedPoint: NSPoint { NSPoint(x: initialFrame.midX, y: initialFrame.midY) }
    func rect(_ value: NSRect) -> [Double] { [value.minX, value.minY, value.width, value.height] }
    func change(_ kind: String, event: NSEvent) {
        hoverKind = kind
        eventPoint = event.locationInWindow
        guard kind == "moved", !changed else { return }
        switch mode {
        case "expand":
            changed = true
            target.frame = initialFrame.insetBy(dx: -25, dy: -15)
        case "away":
            changed = true
            target.setFrameOrigin(NSPoint(x: initialFrame.minX + 260, y: initialFrame.minY))
        case "cover":
            changed = true
            decoy.frame = initialFrame
            window.contentView!.addSubview(decoy, positioned: .above, relativeTo: target)
        default: break
        }
        target.needsDisplay = true
    }
    func record() {
        guard let window else { return }
        let hit = window.contentView!.hitTest(fixedPoint)
        let screenPoint = window.convertPoint(toScreen: fixedPoint)
        let axHit = window.accessibilityHitTest(screenPoint) as AnyObject?
        let value: [String: Any] = [
            "pid": getpid(), "active": NSApp.isActive, "lastCommand": lastCommand, "mode": mode,
            "changed": changed, "hoverKind": hoverKind,
            "entered": target.entered, "moved": target.moved, "downs": target.downs, "ups": target.ups,
            "decoyDowns": decoy.downs, "decoyUps": decoy.ups, "textWrites": target.textWrites,
            "other": other.stringValue, "initialFrame": rect(initialFrame), "frame": rect(target.frame),
            "fixedPoint": [fixedPoint.x, fixedPoint.y], "fixedPointInTarget": target.frame.contains(fixedPoint),
            "viewHitIsTarget": hit === target, "viewHitIsDecoy": hit === decoy,
            "axHitIsTarget": axHit === target, "axHitIsDecoy": axHit === decoy,
            "targetIdentity": String(describing: ObjectIdentifier(target)),
            "eventPoint": eventPoint.map { [$0.x, $0.y] } as Any? ?? NSNull(),
        ]
        try! JSONSerialization.data(withJSONObject: value).write(
            to: folder.appendingPathComponent("state.json"), options: .atomic)
    }
    func tick() {
        if let data = try? Data(contentsOf: folder.appendingPathComponent("command.json")),
            let command = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
            let id = command["id"] as? String, id != lastCommand {
            lastCommand = id
            mode = command["mode"] as? String ?? "stable"
            decoy.removeFromSuperview()
            target.frame = initialFrame
            target.reset()
            decoy.reset()
            changed = false
            hoverKind = ""
            eventPoint = nil
        }
        record()
    }
    func applicationDidFinishLaunching(_ notification: Notification) {
        window = NSWindow(
            contentRect: NSRect(x: 140, y: 160, width: 620, height: 240),
            styleMask: [.titled, .closable], backing: .buffered, defer: false)
        window.title = "Hover pointer probe"
        window.acceptsMouseMovedEvents = true
        target.frame = initialFrame
        target.setAccessibilityElement(true)
        target.setAccessibilityLabel("Hover target")
        target.setAccessibilityIdentifier("hover-original")
        target.hover = { [weak self] kind, event in self?.change(kind, event: event) }
        target.record = { [weak self] in self?.record() }
        decoy.setAccessibilityElement(true)
        decoy.setAccessibilityLabel("Hover decoy")
        decoy.setAccessibilityIdentifier("hover-decoy")
        decoy.record = { [weak self] in self?.record() }
        other.frame = NSRect(x: 20, y: 20, width: 540, height: 24)
        other.setAccessibilityLabel("Unrelated input")
        window.contentView!.addSubview(target)
        window.contentView!.addSubview(other)
        window.orderBack(nil)
        window.makeKey()
        window.makeMain()
        window.makeFirstResponder(other)
        record()
        _ = Timer.scheduledTimer(withTimeInterval: 0.05, repeats: true) { _ in MainActor.assumeIsolated { self.tick() } }
    }
}

@main enum HoverPointerProbe {
    @MainActor static func main() {
        let app = NSApplication.shared
        app.setActivationPolicy(.regular)
        let delegate = HoverPointerDelegate()
        app.delegate = delegate
        app.run()
    }
}
