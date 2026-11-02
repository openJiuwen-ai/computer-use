// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit

/// A mouse-focusable editor whose AX selection is readable but not writable.
/// Its real mouse callback moves the caret; keyboard Select All remains usable.
@MainActor final class PointerFocusInput: NSView, @preconcurrency NSTextInputClient {
    var stringValue = "original"
    var editingRange = NSRange(location: 8, length: 0)
    var marked = NSRange(location: NSNotFound, length: 0)
    var mouseDowns = 0, selectCommands = 0, textCallbacks = 0, valueWrites = 0, selectedTextWrites = 0
    var record: () -> Void = {}

    override var acceptsFirstResponder: Bool { true }
    override func acceptsFirstMouse(for event: NSEvent?) -> Bool { true }
    override func mouseDown(with event: NSEvent) {
        mouseDowns += 1
        window?.makeFirstResponder(self)
        editingRange = NSRange(location: 0, length: 0)
        record()
    }
    override func keyDown(with event: NSEvent) {
        if event.modifierFlags.contains(.command), event.charactersIgnoringModifiers == "a" {
            selectAll(nil)
        } else if event.modifierFlags.contains(.command), event.charactersIgnoringModifiers == "v" {
            insertText(NSPasteboard.general.string(forType: .string) ?? "", replacementRange: editingRange)
        } else {
            interpretKeyEvents([event])
        }
    }
    override func selectAll(_ sender: Any?) {
        selectCommands += 1
        editingRange = NSRange(location: 0, length: (stringValue as NSString).length)
        record()
    }
    func insertText(_ value: Any, replacementRange: NSRange) {
        let text = (value as? NSAttributedString)?.string ?? value as? String ?? ""
        let range = replacementRange.location != NSNotFound ? replacementRange : hasMarkedText() ? marked : editingRange
        stringValue = (stringValue as NSString).replacingCharacters(in: range, with: text)
        editingRange = NSRange(location: range.location + (text as NSString).length, length: 0)
        marked = NSRange(location: NSNotFound, length: 0)
        textCallbacks += 1
        needsDisplay = true
        record()
    }
    func setMarkedText(_ value: Any, selectedRange: NSRange, replacementRange: NSRange) {
        let text = (value as? NSAttributedString)?.string ?? value as? String ?? ""
        let range = replacementRange.location != NSNotFound ? replacementRange : hasMarkedText() ? marked : editingRange
        stringValue = (stringValue as NSString).replacingCharacters(in: range, with: text)
        marked = NSRange(location: range.location, length: (text as NSString).length)
        editingRange = NSRange(location: range.location + selectedRange.location, length: selectedRange.length)
        record()
    }
    func unmarkText() { marked = NSRange(location: NSNotFound, length: 0) }
    func selectedRange() -> NSRange { editingRange }
    func markedRange() -> NSRange { marked }
    func hasMarkedText() -> Bool { marked.location != NSNotFound }
    func validAttributesForMarkedText() -> [NSAttributedString.Key] { [] }
    func attributedSubstring(forProposedRange range: NSRange, actualRange: NSRangePointer?) -> NSAttributedString? {
        let value = stringValue as NSString
        guard range.location <= value.length, range.length <= value.length - range.location else { return nil }
        actualRange?.pointee = range
        return NSAttributedString(string: value.substring(with: range))
    }
    func firstRect(forCharacterRange range: NSRange, actualRange: NSRangePointer?) -> NSRect {
        window?.convertToScreen(convert(bounds, to: nil)) ?? .zero
    }
    func characterIndex(for point: NSPoint) -> Int { 0 }
    override func doCommand(by selector: Selector) {
        if selector == NSSelectorFromString("selectAll:") {
            selectAll(nil)
        } else if selector == NSSelectorFromString("deleteBackward:") {
            insertText("", replacementRange: editingRange)
        }
    }
    override func draw(_ dirtyRect: NSRect) {
        NSColor.textBackgroundColor.setFill()
        bounds.fill()
        stringValue.draw(at: NSPoint(x: 5, y: 8), withAttributes: [.font: NSFont.systemFont(ofSize: 16)])
    }
    override nonisolated func accessibilityRole() -> NSAccessibility.Role? { .textArea }
    override nonisolated func accessibilityValue() -> Any? { MainActor.assumeIsolated { stringValue } }
    override nonisolated func setAccessibilityValue(_ value: Any?) {
        let text = value as? String
        MainActor.assumeIsolated {
            if let text {
                valueWrites += 1
                stringValue = text
                record()
            }
        }
    }
    override nonisolated func isAccessibilityFocused() -> Bool {
        MainActor.assumeIsolated { window?.firstResponder === self }
    }
    override nonisolated func accessibilityNumberOfCharacters() -> Int {
        MainActor.assumeIsolated { (stringValue as NSString).length }
    }
    override nonisolated func accessibilitySelectedTextRange() -> NSRange {
        MainActor.assumeIsolated { editingRange }
    }
    override nonisolated func accessibilitySelectedText() -> String? {
        MainActor.assumeIsolated { (stringValue as NSString).substring(with: editingRange) }
    }
    override nonisolated func setAccessibilitySelectedText(_ value: String?) {
        MainActor.assumeIsolated {
            selectedTextWrites += 1
            insertText(value ?? "", replacementRange: editingRange)
        }
    }
    override nonisolated func accessibilityString(for range: NSRange) -> String? {
        MainActor.assumeIsolated { (stringValue as NSString).substring(with: range) }
    }
    override nonisolated func isAccessibilitySelectorAllowed(_ selector: Selector) -> Bool {
        switch NSStringFromSelector(selector) {
        case "setAccessibilityFocused:", "setAccessibilitySelectedTextRange:", "setAccessibilitySelectedTextRanges:":
            return false
        default: return MainActor.assumeIsolated { super.isAccessibilitySelectorAllowed(selector) }
        }
    }
    func reset() {
        stringValue = "original"
        editingRange = NSRange(location: 8, length: 0)
        marked = NSRange(location: NSNotFound, length: 0)
        mouseDowns = 0
        selectCommands = 0
        textCallbacks = 0
        valueWrites = 0
        selectedTextWrites = 0
        needsDisplay = true
    }
}

@MainActor final class PointerFocusDelegate: NSObject, NSApplicationDelegate {
    let folder = Bundle.main.bundleURL.deletingLastPathComponent()
    var window: NSWindow!
    let input = PointerFocusInput(frame: NSRect(x: 20, y: 75, width: 400, height: 40))
    let other = NSTextField(string: "unchanged")
    var lastCommand = ""

    func record() {
        let value: [String: Any] = [
            "pid": getpid(), "active": NSApp.isActive, "lastCommand": lastCommand,
            "value": input.stringValue, "range": [input.editingRange.location, input.editingRange.length],
            "focused": window.firstResponder === input, "other": other.stringValue,
            "mouseDowns": input.mouseDowns, "selectCommands": input.selectCommands,
            "textCallbacks": input.textCallbacks, "valueWrites": input.valueWrites,
            "selectedTextWrites": input.selectedTextWrites,
        ]
        try! JSONSerialization.data(withJSONObject: value).write(
            to: folder.appendingPathComponent("state.json"), options: .atomic)
    }
    func tick() {
        if let data = try? Data(contentsOf: folder.appendingPathComponent("command.json")),
            let command = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
            let id = command["id"] as? String, id != lastCommand {
            lastCommand = id
            input.reset()
            window.makeFirstResponder(other)
        }
        record()
    }
    func applicationDidFinishLaunching(_ notification: Notification) {
        window = NSWindow(
            contentRect: NSRect(x: 180, y: 170, width: 440, height: 150),
            styleMask: [.titled, .closable], backing: .buffered, defer: false)
        window.title = "Pointer focus probe"
        input.setAccessibilityElement(true)
        input.setAccessibilityLabel("Pointer focus input")
        input.setAccessibilityIdentifier("pointer-focus-input")
        input.record = { [weak self] in self?.record() }
        other.frame = NSRect(x: 20, y: 20, width: 400, height: 25)
        other.setAccessibilityLabel("Unrelated input")
        window.contentView!.addSubview(input)
        window.contentView!.addSubview(other)
        let menu = NSMenu()
        let edit = NSMenuItem(title: "Edit", action: nil, keyEquivalent: "")
        edit.submenu = NSMenu()
        edit.submenu!.addItem(withTitle: "Select All", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")
        menu.addItem(edit)
        NSApp.mainMenu = menu
        window.orderBack(nil)
        window.makeKey()
        window.makeMain()
        window.makeFirstResponder(other)
        record()
        _ = Timer.scheduledTimer(withTimeInterval: 0.05, repeats: true) { _ in MainActor.assumeIsolated { self.tick() } }
    }
}

@main enum NativePointerFocusProbe {
    @MainActor static func main() {
        let app = NSApplication.shared
        app.setActivationPolicy(.regular)
        let delegate = PointerFocusDelegate()
        app.delegate = delegate
        app.run()
    }
}
