// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit

/// A genuine keyboard recipient whose AX role carries no text-editing claim.
@MainActor final class KeyboardScopeCanvas: NSView {
    var leftKeys = 0
    var record: () -> Void = {}
    override var acceptsFirstResponder: Bool { true }
    override func keyDown(with event: NSEvent) {
        if event.keyCode == 123 { leftKeys += 1 }
        record()
    }
    override nonisolated func accessibilityRole() -> NSAccessibility.Role? { .group }
    override nonisolated func isAccessibilityFocused() -> Bool {
        MainActor.assumeIsolated { window?.firstResponder === self }
    }
    override nonisolated func setAccessibilityFocused(_ value: Bool) {
        MainActor.assumeIsolated { if value { window?.makeFirstResponder(self) } }
    }
    override func draw(_ dirtyRect: NSRect) {
        NSColor.controlAccentColor.withAlphaComponent(0.15).setFill()
        bounds.fill()
        "Focused canvas — Left key counter".draw(
            at: NSPoint(x: 12, y: 16), withAttributes: [.font: NSFont.systemFont(ofSize: 16)])
    }
}

/// This decoration is deliberately outside the text field's editing contract.
final class KeyboardScopeDecoration: NSAccessibilityElement, @unchecked Sendable {
    override func accessibilityRole() -> NSAccessibility.Role? { nil }
}

/// The exact field receives Return without discovering its unrelated AX child.
@MainActor final class KeyboardScopeText: NSView {
    var value = "original"
    var returnKeys = 0
    var record: () -> Void = {}
    let decoration = KeyboardScopeDecoration()
    override var acceptsFirstResponder: Bool { true }
    override func keyDown(with event: NSEvent) {
        if event.keyCode == 36 { returnKeys += 1 }
        record()
    }
    override nonisolated func accessibilityRole() -> NSAccessibility.Role? { .textField }
    override nonisolated func accessibilityValue() -> Any? { MainActor.assumeIsolated { value } }
    override nonisolated func setAccessibilityValue(_ newValue: Any?) {
        let string = newValue as? String
        MainActor.assumeIsolated {
            if let string { value = string; record() }
        }
    }
    override nonisolated func isAccessibilityFocused() -> Bool {
        MainActor.assumeIsolated { window?.firstResponder === self }
    }
    override nonisolated func setAccessibilityFocused(_ value: Bool) {
        MainActor.assumeIsolated { if value { window?.makeFirstResponder(self) } }
    }
    override nonisolated func accessibilityChildren() -> [Any]? {
        [MainActor.assumeIsolated { decoration }]
    }
    override func draw(_ dirtyRect: NSRect) {
        NSColor.textBackgroundColor.setFill()
        bounds.fill()
        "Text field — Return callback: \(returnKeys)".draw(
            at: NSPoint(x: 12, y: 12), withAttributes: [.font: NSFont.systemFont(ofSize: 16)])
    }
}

@MainActor final class KeyboardScopeDelegate: NSObject, NSApplicationDelegate {
    let folder = Bundle.main.bundleURL.deletingLastPathComponent()
    var window: NSWindow!
    let scope = NSView(frame: NSRect(x: 20, y: 150, width: 480, height: 90))
    let canvas = KeyboardScopeCanvas(frame: NSRect(x: 10, y: 15, width: 460, height: 60))
    let field = KeyboardScopeText(frame: NSRect(x: 20, y: 85, width: 480, height: 42))
    let other = NSTextField(string: "unchanged")
    var lastCommand = ""

    func record() {
        guard let window else { return }
        let value: [String: Any] = [
            "pid": getpid(), "window": window.windowNumber, "active": NSApp.isActive,
            "keyWindow": window.isKeyWindow, "canvasFocused": window.firstResponder === canvas,
            "fieldFocused": window.firstResponder === field, "leftKeys": canvas.leftKeys,
            "returnKeys": field.returnKeys, "field": field.value, "other": other.stringValue,
            "commandId": lastCommand,
        ]
        try! JSONSerialization.data(withJSONObject: value, options: [.sortedKeys]).write(
            to: folder.appendingPathComponent("state.json"), options: .atomic)
    }

    func tick() {
        if let data = try? Data(contentsOf: folder.appendingPathComponent("command.json")),
            let command = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
            let id = command["id"] as? String, id != lastCommand
        {
            lastCommand = id
            if command["kind"] as? String == "field" { window.makeFirstResponder(field) }
            if command["kind"] as? String == "canvas" { window.makeFirstResponder(canvas) }
        }
        record()
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        window = NSWindow(
            contentRect: NSRect(x: 180, y: 160, width: 520, height: 265),
            styleMask: [.titled, .closable], backing: .buffered, defer: false)
        window.title = "Keyboard scope probe"
        window.tabbingMode = .disallowed
        scope.setAccessibilityElement(true)
        scope.setAccessibilityRole(.group)
        scope.setAccessibilityLabel("Keyboard scope")
        canvas.setAccessibilityElement(true)
        canvas.setAccessibilityLabel("Focused canvas")
        canvas.record = { [weak self] in self?.record() }
        scope.addSubview(canvas)
        field.setAccessibilityElement(true)
        field.setAccessibilityLabel("Opaque child input")
        field.decoration.setAccessibilityLabel("Unrelated opaque decoration")
        field.decoration.setAccessibilityParent(field)
        field.record = { [weak self] in self?.record() }
        other.frame = NSRect(x: 20, y: 25, width: 480, height: 28)
        other.setAccessibilityLabel("Unrelated input")
        for view in [scope, field, other] { window.contentView!.addSubview(view) }
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
        window.makeFirstResponder(canvas)
        record()
        Timer.scheduledTimer(withTimeInterval: 0.05, repeats: true) { _ in
            MainActor.assumeIsolated { self.tick() }
        }
    }
}

@main enum KeyboardScopeProbe {
    @MainActor static func main() {
        let application = NSApplication.shared
        application.setActivationPolicy(.regular)
        let delegate = KeyboardScopeDelegate()
        application.delegate = delegate
        application.run()
    }
}
