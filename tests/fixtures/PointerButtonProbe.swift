// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import CoreGraphics
import CryptoKit

@MainActor final class PointerButtonApplication: NSApplication {
    var inputEvents = [[String: Any]]()
    override func sendEvent(_ event: NSEvent) {
        let buttons: Set<NSEvent.EventType> = [
            .leftMouseDown, .leftMouseUp, .rightMouseDown, .rightMouseUp, .otherMouseDown, .otherMouseUp
        ]
        if buttons.contains(event.type) {
            inputEvents.append([
                "kind": "mouse", "type": event.type.rawValue, "button": event.buttonNumber,
                "clickCount": event.clickCount, "window": event.windowNumber,
                "point": [event.locationInWindow.x, event.locationInWindow.y], "timestamp": event.timestamp
            ])
        } else if [.keyDown, .keyUp, .flagsChanged].contains(event.type) {
            inputEvents.append([
                "kind": "keyboard", "type": event.type.rawValue, "code": event.keyCode,
                "flags": event.modifierFlags.rawValue, "window": event.windowNumber
            ])
        }
        super.sendEvent(event)
    }
}

/// These independent callbacks are the oracle, not AXPress or an SDK receipt.
@MainActor final class PointerButtonSurface: NSView {
    let title: String
    let exposesAccessibility: Bool
    var callbacks = [[String: Any]]()
    var changed: () -> Void = {}

    init(frame: NSRect, title: String, exposesAccessibility: Bool = true) {
        self.title = title
        self.exposesAccessibility = exposesAccessibility
        super.init(frame: frame)
        setAccessibilityElement(exposesAccessibility)
        setAccessibilityRole(.group)
        setAccessibilityLabel(title)
    }
    required init?(coder: NSCoder) { fatalError("init(coder:) has not been implemented") }
    override func acceptsFirstMouse(for event: NSEvent?) -> Bool { true }
    override func accessibilityHitTest(_ point: NSPoint) -> Any? {
        if exposesAccessibility { return super.accessibilityHitTest(point) }
        return MainActor.assumeIsolated { window }
    }

    func record(_ name: String, _ event: NSEvent) {
        callbacks.append([
            "callback": name, "type": event.type.rawValue, "button": event.buttonNumber,
            "clickCount": event.clickCount, "window": event.windowNumber,
            "point": [event.locationInWindow.x, event.locationInWindow.y], "timestamp": event.timestamp
        ])
        needsDisplay = true
        changed()
    }
    override func mouseDown(with event: NSEvent) { record("mouseDown", event) }
    override func mouseUp(with event: NSEvent) { record("mouseUp", event) }
    override func rightMouseDown(with event: NSEvent) { record("rightMouseDown", event) }
    override func rightMouseUp(with event: NSEvent) { record("rightMouseUp", event) }
    override func otherMouseDown(with event: NSEvent) { record("otherMouseDown", event) }
    override func otherMouseUp(with event: NSEvent) { record("otherMouseUp", event) }

    override func draw(_ dirtyRect: NSRect) {
        NSColor.controlAccentColor.withAlphaComponent(0.15).setFill()
        bounds.fill()
        let text = title + "\nCallbacks: \(callbacks.count)"
        text.draw(in: bounds.insetBy(dx: 14, dy: 18), withAttributes: [.font: NSFont.systemFont(ofSize: 17)])
    }
}

@MainActor final class PointerButtonDelegate: NSObject, NSApplicationDelegate {
    let folder = Bundle.main.bundleURL.deletingLastPathComponent()
    var window: NSWindow!
    let target = PointerButtonSurface(
        frame: NSRect(x: 20, y: 80, width: 320, height: 180), title: "Pointer target",
        exposesAccessibility: !CommandLine.arguments.contains("--weak-ax"))
    let guardSurface = PointerButtonSurface(
        frame: NSRect(x: 365, y: 80, width: 240, height: 180), title: "Unrelated guard")
    let guardField = NSTextField(string: "unchanged guard text")

    func digest() -> String {
        var hash = SHA256()
        for item in NSPasteboard.general.pasteboardItems ?? [] {
            for type in item.types.sorted(by: { $0.rawValue < $1.rawValue }) {
                hash.update(data: Data(type.rawValue.utf8))
                if let data = item.data(forType: type) { hash.update(data: data) }
            }
        }
        return hash.finalize().map { String(format: "%02x", $0) }.joined()
    }
    func record() {
        guard let window else { return }
        let screen = window.convertPoint(toScreen: target.convert(
            NSPoint(x: target.bounds.midX, y: target.bounds.midY), to: nil))
        let cursor = CGEvent(source: nil)?.location
        let value: [String: Any] = [
            "pid": getpid(), "window": window.windowNumber, "active": NSApp.isActive,
            "weakAX": !target.exposesAccessibility,
            "frontmostPID": NSWorkspace.shared.frontmostApplication?.processIdentifier as Any? ?? NSNull(),
            "width": window.frame.width, "height": window.frame.height,
            "targetPoint": [screen.x - window.frame.minX, window.frame.maxY - screen.y],
            "callbacks": target.callbacks, "guardCallbacks": guardSurface.callbacks,
            "guardText": guardField.stringValue,
            "inputEvents": (NSApp as? PointerButtonApplication)?.inputEvents ?? [],
            "cursor": cursor.map { [$0.x, $0.y] } as Any? ?? NSNull(),
            "heldButtons": [
                "left": CGEventSource.buttonState(.combinedSessionState, button: .left),
                "right": CGEventSource.buttonState(.combinedSessionState, button: .right),
                "middle": CGEventSource.buttonState(.combinedSessionState, button: .center)
            ],
            "clipboardDigest": digest(), "clipboardChangeCount": NSPasteboard.general.changeCount
        ]
        try? JSONSerialization.data(withJSONObject: value, options: [.sortedKeys]).write(
            to: folder.appendingPathComponent("state.json"), options: .atomic)
    }
    func applicationDidFinishLaunching(_ notification: Notification) {
        window = NSWindow(
            contentRect: NSRect(x: 180, y: 180, width: 625, height: 340),
            styleMask: [.titled, .closable], backing: .buffered, defer: false)
        window.title = "Pointer button probe"
        window.tabbingMode = .disallowed
        window.isReleasedWhenClosed = false
        guardField.frame = NSRect(x: 20, y: 285, width: 585, height: 28)
        guardField.setAccessibilityLabel("Unrelated guard input")
        target.changed = { [weak self] in self?.record() }
        guardSurface.changed = { [weak self] in self?.record() }
        for view in [target, guardSurface, guardField] { window.contentView!.addSubview(view) }
        let menu = NSMenu()
        let appMenu = NSMenuItem(title: "PointerButtonProbe", action: nil, keyEquivalent: "")
        appMenu.submenu = NSMenu()
        appMenu.submenu!.addItem(withTitle: "Quit", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        menu.addItem(appMenu)
        NSApp.mainMenu = menu
        window.orderBack(nil)
        window.makeKey()
        window.makeMain()
        window.makeFirstResponder(guardField)
        record()
        _ = Timer.scheduledTimer(withTimeInterval: 0.05, repeats: true) { _ in
            MainActor.assumeIsolated { self.record() }
        }
    }
}

@main enum PointerButtonProbe {
    @MainActor static func main() {
        let app = PointerButtonApplication.shared
        app.setActivationPolicy(.regular)
        let delegate = PointerButtonDelegate()
        app.delegate = delegate
        app.run()
    }
}
