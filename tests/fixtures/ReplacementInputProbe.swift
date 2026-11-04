// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit

/// The AX extent and the keyboard editor's active selection are independent.
@MainActor final class ReplacementInput: NSView, @preconcurrency NSTextInputClient {
    var stringValue = "original"
    var editingRange = NSRange(location: 8, length: 0)
    var reportedRange: NSRange?
    var marked = NSRange(location: NSNotFound, length: 0)
    var selectionWrites = 0, selectCommands = 0, characters = 0
    var ready = false, stealFocus = false
    var record: () -> Void = {}
    weak var other: NSTextField?
    convenience init(string: String) {
        self.init(frame: .zero)
        stringValue = string
    }
    override var acceptsFirstResponder: Bool { true }
    override func keyDown(with event: NSEvent) {
        if event.modifierFlags.contains(.command), event.charactersIgnoringModifiers == "a" {
            selectAll(nil)
        } else {
            interpretKeyEvents([event])
        }
    }
    override func selectAll(_ sender: Any?) {
        selectCommands += 1
        editingRange = NSRange(location: 0, length: (stringValue as NSString).length)
        reportedRange = nil
        ready = true
        if stealFocus { window?.makeFirstResponder(other) }
        record()
    }
    func insertText(_ value: Any, replacementRange: NSRange) {
        guard ready else { return }
        let text = (value as? NSAttributedString)?.string ?? value as? String ?? ""
        let range = replacementRange.location != NSNotFound ? replacementRange : hasMarkedText() ? marked : editingRange
        stringValue = (stringValue as NSString).replacingCharacters(in: range, with: text)
        editingRange = NSRange(location: range.location + (text as NSString).length, length: 0)
        marked = NSRange(location: NSNotFound, length: 0)
        characters += 1
        needsDisplay = true
        record()
    }
    func setMarkedText(_ value: Any, selectedRange: NSRange, replacementRange: NSRange) {
        guard ready else { return }
        let text = (value as? NSAttributedString)?.string ?? value as? String ?? ""
        let range = replacementRange.location != NSNotFound ? replacementRange : hasMarkedText() ? marked : editingRange
        stringValue = (stringValue as NSString).replacingCharacters(in: range, with: text)
        marked = NSRange(location: range.location, length: (text as NSString).length)
        editingRange = NSRange(location: range.location + selectedRange.location, length: selectedRange.length)
        needsDisplay = true
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
        stringValue.draw(at: NSPoint(x: 5, y: 5), withAttributes: [.font: NSFont.systemFont(ofSize: 16)])
    }
    override nonisolated func accessibilityRole() -> NSAccessibility.Role? { .incrementor }
    override nonisolated func accessibilityValue() -> Any? { MainActor.assumeIsolated { stringValue } }
    override nonisolated func setAccessibilityValue(_ value: Any?) {
        let string = value as? String
        MainActor.assumeIsolated {
            if let string {
                stringValue = string
                record()
            }
        }
    }
    override nonisolated func isAccessibilityFocused() -> Bool {
        MainActor.assumeIsolated { window?.firstResponder === self }
    }
    override nonisolated func setAccessibilityFocused(_ value: Bool) {
        MainActor.assumeIsolated { if value { window?.makeFirstResponder(self) } }
    }
    override nonisolated func accessibilityNumberOfCharacters() -> Int {
        MainActor.assumeIsolated { (stringValue as NSString).length }
    }
    override nonisolated func accessibilitySelectedTextRange() -> NSRange {
        MainActor.assumeIsolated { reportedRange ?? editingRange }
    }
    override nonisolated func setAccessibilitySelectedTextRange(_ range: NSRange) {
        MainActor.assumeIsolated {
            reportedRange = range
            selectionWrites += 1
            ready = false
            record()
        }
    }
    override nonisolated func accessibilitySelectedText() -> String? {
        MainActor.assumeIsolated { (stringValue as NSString).substring(with: reportedRange ?? editingRange) }
    }
    override nonisolated func accessibilityString(for range: NSRange) -> String? {
        MainActor.assumeIsolated { (stringValue as NSString).substring(with: range) }
    }
}

@MainActor final class ReplacementInputDelegate: NSObject, NSApplicationDelegate {
    let folder = Bundle.main.bundleURL.deletingLastPathComponent()
    var window: NSWindow!
    let input = ReplacementInput(string: "original")
    let selected = ReplacementInput(string: "original")
    let other = NSTextField(string: "unchanged")
    func record() {
        func state(_ field: ReplacementInput) -> [String: Any] {
            [
                "value": field.stringValue, "selectionWrites": field.selectionWrites,
                "selectCommands": field.selectCommands, "characters": field.characters,
            ]
        }
        let value: [String: Any] = [
            "pid": getpid(), "input": state(input), "selected": state(selected), "other": other.stringValue,
        ]
        try! JSONSerialization.data(withJSONObject: value).write(
            to: folder.appendingPathComponent("state.json"), options: .atomic)
    }
    func applicationDidFinishLaunching(_ notification: Notification) {
        window = NSWindow(
            contentRect: NSRect(x: 180, y: 170, width: 440, height: 160),
            styleMask: [.titled, .closable], backing: .buffered, defer: false)
        window.title = "Replacement input probe"
        for (field, label, y) in [(input, "Replacement input", 100.0), (selected, "Reported full selection", 60.0)] {
            field.frame = NSRect(x: 20, y: y, width: 400, height: 30)
            field.setAccessibilityElement(true)
            field.setAccessibilityLabel(label)
            field.other = other
            field.record = { [weak self] in self?.record() }
            window.contentView!.addSubview(field)
        }
        selected.reportedRange = NSRange(location: 0, length: (selected.stringValue as NSString).length)
        other.frame = NSRect(x: 20, y: 20, width: 400, height: 25)
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
        Timer.scheduledTimer(withTimeInterval: 0.05, repeats: true) { _ in
            MainActor.assumeIsolated {
                self.input.stealFocus = FileManager.default.fileExists(
                    atPath: self.folder.appendingPathComponent("steal-focus").path)
                self.record()
            }
        }
    }
}

@main enum ReplacementInputProbe {
    @MainActor static func main() {
        let app = NSApplication.shared
        app.setActivationPolicy(.regular)
        let delegate = ReplacementInputDelegate()
        app.delegate = delegate
        app.run()
    }
}
