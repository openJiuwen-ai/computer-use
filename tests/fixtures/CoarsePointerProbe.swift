// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit

/// The accessibility provider deliberately exposes an option in its tree but
/// returns its original window from hit testing. Actual mouse delivery has an
/// independent callback, so a driver receipt is never the acceptance oracle.
@MainActor final class CoarsePointerWindow: NSWindow {
    var coarse = true
    var exact = false
    var pointerEvents = [[String: Any]]()
    override func sendEvent(_ event: NSEvent) {
        if [.mouseMoved, .leftMouseDown, .leftMouseUp, .leftMouseDragged, .scrollWheel].contains(event.type) {
            pointerEvents.append([
                "type": event.type.rawValue, "window": event.windowNumber,
                "local": [event.locationInWindow.x, event.locationInWindow.y],
                "cgPoint": event.cgEvent.map { [$0.location.x, $0.location.y] } ?? []
            ])
        }
        super.sendEvent(event)
    }
    weak var guardButton: NSButton?
    override func accessibilityHitTest(_ point: NSPoint) -> Any? {
        if MainActor.assumeIsolated({ exact }) { return MainActor.assumeIsolated { guardButton } }
        if MainActor.assumeIsolated({ coarse }) { return self }
        let button: NSButton? = MainActor.assumeIsolated { guardButton }
        return button
    }
}

@MainActor final class CoarsePointerOption: NSView {
    var onClick: () -> Void = {}
    override var isFlipped: Bool { true }
    override func acceptsFirstMouse(for event: NSEvent?) -> Bool { false }
    override func mouseDown(with event: NSEvent) { onClick() }
    override func draw(_ dirtyRect: NSRect) {
        NSColor.controlBackgroundColor.setFill()
        bounds.fill()
        "45 minutes".draw(
            at: NSPoint(x: 12, y: 8),
            withAttributes: [.font: NSFont.systemFont(ofSize: 18), .foregroundColor: NSColor.labelColor])
    }
}

@MainActor final class CoarsePointerDelegate: NSObject, NSApplicationDelegate {
    let folder = Bundle.main.bundleURL.deletingLastPathComponent()
    var window: CoarsePointerWindow!
    var option: CoarsePointerOption!
    var guardButton: NSButton!
    var optionClicks = 0, guardClicks = 0
    var lastCommand = ""
    func record() {
        guard let window, let option else { return }
        let point = window.convertPoint(
            toScreen: option.convert(NSPoint(x: option.bounds.midX, y: option.bounds.midY), to: nil))
        let value: [String: Any] = [
            "pid": getpid(), "window": window.windowNumber, "active": NSApp.isActive, "key": window.isKeyWindow,
            "mode": window.exact ? "button" : window.coarse ? "coarse" : "sibling", "optionClicks": optionClicks,
            "guardClicks": guardClicks, "point": [point.x, NSScreen.screens[0].frame.maxY - point.y],
            "imagePoint": [point.x - window.frame.minX, window.frame.maxY - point.y],
            "pointerEvents": window.pointerEvents
        ]
        try? JSONSerialization.data(withJSONObject: value, options: [.sortedKeys]).write(
            to: folder.appendingPathComponent("state.json"), options: .atomic)
    }
    @objc func hitGuard() {
        guardClicks += 1
        record()
    }
    func tick() {
        if let data = try? Data(contentsOf: folder.appendingPathComponent("command.json")),
            let command = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
            let id = command["id"] as? String, id != lastCommand
        {
            lastCommand = id
            window.exact = command["kind"] as? String == "button"
            window.coarse = command["kind"] as? String != "sibling"
            guardButton.isHidden = window.coarse && !window.exact
        }
        record()
    }
    func applicationDidFinishLaunching(_ notification: Notification) {
        window = CoarsePointerWindow(
            contentRect: NSRect(x: 160, y: 200, width: 460, height: 240), styleMask: [.titled, .closable],
            backing: .buffered, defer: false)
        window.title = "Coarse pointer probe"
        window.isReleasedWhenClosed = false
        window.tabbingMode = .disallowed
        let list = NSView(frame: NSRect(x: 30, y: 70, width: 400, height: 110))
        list.setAccessibilityElement(true)
        list.setAccessibilityRole(.list)
        list.setAccessibilityLabel("Duration options")
        option = CoarsePointerOption(frame: NSRect(x: 15, y: 35, width: 370, height: 40))
        option.setAccessibilityElement(true)
        option.setAccessibilityRole(.staticText)
        option.setAccessibilityValue("45 minutes")
        option.setAccessibilityIdentifier("coarse-duration-option")
        option.onClick = { [weak self] in
            self?.optionClicks += 1
            self?.record()
        }
        list.addSubview(option)
        guardButton = NSButton(title: "Unrelated guard", target: self, action: #selector(hitGuard))
        guardButton.frame = option.frame
        guardButton.isHidden = true
        list.addSubview(guardButton)
        window.guardButton = guardButton
        window.contentView!.addSubview(list)
        window.orderBack(nil)
        window.makeKey()
        window.makeMain()
        record()
        _ = Timer.scheduledTimer(withTimeInterval: 0.05, repeats: true) { _ in MainActor.assumeIsolated { self.tick() }
        }
    }
}

@main enum CoarsePointerProbe {
    @MainActor static func main() {
        let application = NSApplication.shared
        application.setActivationPolicy(.regular)
        let delegate = CoarsePointerDelegate()
        application.delegate = delegate
        application.run()
    }
}
