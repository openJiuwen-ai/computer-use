// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit

/// An editable child with a real keyboard selection and observable input callbacks.
@MainActor final class RowIdentityField: NSView, @preconcurrency NSTextInputClient {
    var value = "seed"
    var editingRange = NSRange(location: 4, length: 0)
    var marked = NSRange(location: NSNotFound, length: 0)
    var callbacks = 0, valueWrites = 0, tabKeys = 0, returnKeys = 0
    var record: () -> Void = {}
    weak var other: NSTextField?

    override var acceptsFirstResponder: Bool { true }

    func assign(_ string: String, reset: Bool) {
        value = string
        editingRange = NSRange(location: (string as NSString).length, length: 0)
        marked = NSRange(location: NSNotFound, length: 0)
        if reset { callbacks = 0; valueWrites = 0; tabKeys = 0; returnKeys = 0 }
        needsDisplay = true
    }

    override func keyDown(with event: NSEvent) {
        switch event.keyCode {
        case 48:
            tabKeys += 1
            window?.makeFirstResponder(other)
            record()
        case 36, 76:
            returnKeys += 1
            record()
        default:
            if event.modifierFlags.contains(.command), event.charactersIgnoringModifiers == "a" {
                selectAll(nil)
            } else if !event.modifierFlags.intersection([.command, .control]).isEmpty {
                interpretKeyEvents([event])
            } else if let characters = event.characters, !characters.isEmpty,
                      characters.unicodeScalars.allSatisfy({ $0.value >= 32 && $0.value < 127 })
            {
                // ASCII intentionally bypasses the user's active input method.
                insertText(characters, replacementRange: NSRange(location: NSNotFound, length: 0))
            } else {
                interpretKeyEvents([event])
            }
        }
    }

    override func selectAll(_ sender: Any?) {
        editingRange = NSRange(location: 0, length: (value as NSString).length)
        marked = NSRange(location: NSNotFound, length: 0)
        record()
    }

    func valid(_ range: NSRange) -> Bool {
        let length = (value as NSString).length
        return range.location != NSNotFound && range.location <= length && range.length <= length - range.location
    }

    func insertText(_ input: Any, replacementRange: NSRange) {
        let text = (input as? NSAttributedString)?.string ?? input as? String ?? ""
        let range = replacementRange.location != NSNotFound ? replacementRange : hasMarkedText() ? marked : editingRange
        guard valid(range) else { return }
        value = (value as NSString).replacingCharacters(in: range, with: text)
        editingRange = NSRange(location: range.location + (text as NSString).length, length: 0)
        marked = NSRange(location: NSNotFound, length: 0)
        callbacks += 1
        needsDisplay = true
        record()
    }

    func setMarkedText(_ input: Any, selectedRange: NSRange, replacementRange: NSRange) {
        let text = (input as? NSAttributedString)?.string ?? input as? String ?? ""
        let range = replacementRange.location != NSNotFound ? replacementRange : hasMarkedText() ? marked : editingRange
        guard valid(range), selectedRange.location <= (text as NSString).length,
              selectedRange.length <= (text as NSString).length - selectedRange.location else { return }
        value = (value as NSString).replacingCharacters(in: range, with: text)
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
        guard valid(range) else { return nil }
        actualRange?.pointee = range
        return NSAttributedString(string: (value as NSString).substring(with: range))
    }
    func firstRect(forCharacterRange range: NSRange, actualRange: NSRangePointer?) -> NSRect {
        if valid(range) { actualRange?.pointee = range }
        return window?.convertToScreen(convert(bounds, to: nil)) ?? .zero
    }
    func characterIndex(for point: NSPoint) -> Int { 0 }

    override func doCommand(by selector: Selector) {
        switch NSStringFromSelector(selector) {
        case "selectAll:": selectAll(nil)
        case "insertTab:":
            tabKeys += 1
            window?.makeFirstResponder(other)
            record()
        case "insertNewline:": returnKeys += 1; record()
        case "deleteBackward:":
            var range = editingRange
            if range.length == 0, range.location > 0 {
                range = (value as NSString).rangeOfComposedCharacterSequence(at: range.location - 1)
            }
            insertText("", replacementRange: range)
        default: break
        }
    }

    override nonisolated func accessibilityRole() -> NSAccessibility.Role? { .textField }
    override nonisolated func accessibilityLabel() -> String? { "Row identity input" }
    override nonisolated func accessibilityIdentifier() -> String { "row-field-control" }
    override nonisolated func accessibilityValue() -> Any? { MainActor.assumeIsolated { value } }
    override nonisolated func setAccessibilityValue(_ input: Any?) {
        let string = input as? String
        MainActor.assumeIsolated {
            guard let string else { return }
            valueWrites += 1
            assign(string, reset: false)
            record()
        }
    }
    override nonisolated func isAccessibilityFocused() -> Bool {
        MainActor.assumeIsolated { window?.firstResponder === self }
    }
    override nonisolated func setAccessibilityFocused(_ focused: Bool) {
        MainActor.assumeIsolated {
            if focused { window?.makeFirstResponder(self) }
            record()
        }
    }
    override nonisolated func accessibilityNumberOfCharacters() -> Int {
        MainActor.assumeIsolated { (value as NSString).length }
    }
    override nonisolated func accessibilitySelectedTextRange() -> NSRange {
        MainActor.assumeIsolated { editingRange }
    }
    override nonisolated func setAccessibilitySelectedTextRange(_ range: NSRange) {
        MainActor.assumeIsolated {
            guard valid(range) else { return }
            editingRange = range
            marked = NSRange(location: NSNotFound, length: 0)
            record()
        }
    }
    override nonisolated func accessibilitySelectedText() -> String? {
        MainActor.assumeIsolated { (value as NSString).substring(with: editingRange) }
    }
    override nonisolated func accessibilityString(for range: NSRange) -> String? {
        MainActor.assumeIsolated { valid(range) ? (value as NSString).substring(with: range) : nil }
    }
    override nonisolated func isAccessibilitySelectorAllowed(_ selector: Selector) -> Bool {
        // No AXSelectedText setter: insertion must reach the real keyboard callback.
        if selector == NSSelectorFromString("setAccessibilitySelectedText:") { return false }
        return MainActor.assumeIsolated { super.isAccessibilitySelectorAllowed(selector) }
    }
    override func draw(_ dirtyRect: NSRect) {
        NSColor.textBackgroundColor.setFill()
        bounds.fill()
        value.draw(at: NSPoint(x: 8, y: 8), withAttributes: [.font: NSFont.systemFont(ofSize: 16)])
    }
}

@MainActor final class RowIdentityCell: NSView {
    let field = RowIdentityField(frame: NSRect(x: 8, y: 8, width: 388, height: 36))
    var rowIndexRange = NSRange(location: 0, length: 1)
    override init(frame frameRect: NSRect) {
        super.init(frame: frameRect)
        setAccessibilityElement(true)
        field.setAccessibilityElement(true)
        addSubview(field)
    }
    required init?(coder: NSCoder) { fatalError("init(coder:) has not been implemented") }
    override nonisolated func accessibilityRole() -> NSAccessibility.Role? { .cell }
    override nonisolated func accessibilityLabel() -> String? { "Row identity cell" }
    override nonisolated func accessibilityRowIndexRange() -> NSRange {
        MainActor.assumeIsolated { rowIndexRange }
    }
    override nonisolated func accessibilityColumnIndexRange() -> NSRange { NSRange(location: 0, length: 1) }
    override nonisolated func accessibilityChildren() -> [Any]? { [MainActor.assumeIsolated { field }] }
}

@MainActor final class RowIdentityRow: NSView {
    let cell = RowIdentityCell(frame: NSRect(x: 8, y: 8, width: 404, height: 52))
    var dataKey = "A", publishing = "record"
    var derivedDescription = true
    var descriptionOverride: String?
    var rowDescription: String {
        descriptionOverride ?? (derivedDescription ? "Record \(dataKey): \(cell.field.value)" : "Row identity record")
    }
    override init(frame frameRect: NSRect) {
        super.init(frame: frameRect)
        setAccessibilityElement(true)
        addSubview(cell)
    }
    required init?(coder: NSCoder) { fatalError("init(coder:) has not been implemented") }
    override nonisolated func accessibilityRole() -> NSAccessibility.Role? { .row }
    override nonisolated func accessibilityLabel() -> String? {
        // NSAccessibilityProtocols.h maps accessibilityLabel to AXDescription.
        MainActor.assumeIsolated { rowDescription }
    }
    override nonisolated func accessibilityIdentifier() -> String {
        // This fixture's record-key promise is not a general AXIdentifier rule.
        MainActor.assumeIsolated { publishing == "record" ? "data-record-\(dataKey)" : "row-template" }
    }
    override nonisolated func accessibilityChildren() -> [Any]? { [MainActor.assumeIsolated { cell }] }
}

@MainActor final class RowIdentityDelegate: NSObject, NSApplicationDelegate {
    let folder = Bundle.main.bundleURL.deletingLastPathComponent()
    let other = NSTextField(string: "unchanged")
    var window: NSWindow!
    var row: RowIdentityRow!
    weak var oldRow: RowIdentityRow?
    weak var oldField: RowIdentityField?
    var hasReplaced = false
    var commandId = ""

    func install(dataKey: String, publishing: String, derivedDescription: Bool, value: String) {
        let fresh = RowIdentityRow(frame: NSRect(x: 20, y: 75, width: 420, height: 68))
        fresh.dataKey = dataKey
        fresh.publishing = publishing
        fresh.derivedDescription = derivedDescription
        fresh.cell.field.assign(value, reset: true)
        fresh.cell.field.other = other
        fresh.cell.field.record = { [weak self] in self?.record() }
        row = fresh
        window.contentView!.addSubview(fresh)
    }

    func record() {
        guard let row, let window else { return }
        let field = row.cell.field
        var state: [String: Any] = [
            "pid": getpid(), "commandId": commandId, "dataKey": row.dataKey,
            "publishing": row.publishing, "derivedDescription": row.derivedDescription,
            "rowDescription": row.rowDescription, "value": field.value,
            "descriptionOverride": row.descriptionOverride as Any? ?? NSNull(),
            "cellRowIndexRange": ["location": row.cell.rowIndexRange.location, "length": row.cell.rowIndexRange.length],
            "callbacks": field.callbacks, "valueWrites": field.valueWrites,
            "tabKeys": field.tabKeys, "returnKeys": field.returnKeys,
            "rowObjectID": String(describing: ObjectIdentifier(row)),
            "cellObjectID": String(describing: ObjectIdentifier(row.cell)),
            "fieldObjectID": String(describing: ObjectIdentifier(field)),
            "fieldFocused": window.firstResponder === field, "other": other.stringValue,
        ]
        if hasReplaced {
            state["oldRowAlive"] = oldRow != nil
            state["oldFieldAlive"] = oldField != nil
        }
        try! JSONSerialization.data(withJSONObject: state, options: [.sortedKeys]).write(
            to: folder.appendingPathComponent("state.json"), options: .atomic)
    }

    func nativeInsert(_ text: String) {
        window.makeFirstResponder(row.cell.field)
        // Independent receiver-local control, not an OS background injection test.
        // Exercise NSWindow's normal keyboard responder path and the real callback
        // while retaining the same native views and fixture model record.
        for character in text {
            guard let event = NSEvent.keyEvent(
                with: .keyDown, location: .zero, modifierFlags: [], timestamp: 0,
                windowNumber: window.windowNumber, context: nil,
                characters: String(character), charactersIgnoringModifiers: String(character),
                isARepeat: false, keyCode: 0) else { continue }
            window.sendEvent(event)
        }
    }

    func tick() {
        guard let data = try? Data(contentsOf: folder.appendingPathComponent("command.json")),
              let command = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let id = command["id"] as? String, id != commandId else { record(); return }
        commandId = id
        switch command["kind"] as? String ?? "configure" {
        case "configure":
            row.dataKey = command["dataKey"] as? String ?? "A"
            row.publishing = command["publishing"] as? String == "static" ? "static" : "record"
            row.derivedDescription = command["derivedDescription"] as? Bool ?? true
            row.descriptionOverride = nil
            row.cell.rowIndexRange = NSRange(location: 0, length: 1)
            row.cell.field.assign(command["value"] as? String ?? "seed", reset: true)
            window.makeFirstResponder(row.cell.field)
        case "reuse":
            row.dataKey = command["dataKey"] as? String ?? "B"
            row.cell.field.assign(command["value"] as? String ?? "other-record", reset: false)
        case "description":
            row.descriptionOverride = command["text"] as? String ?? "Updated row presentation"
        case "cell_position":
            if let index = command["rowIndex"] as? Int, index >= 0 {
                row.cell.rowIndexRange = NSRange(location: index, length: 1)
            }
        case "replace":
            window.makeFirstResponder(other)
            let previous = row!
            oldRow = previous
            oldField = previous.cell.field
            hasReplaced = true
            previous.removeFromSuperview()
            row = nil
            // Allocate the replacement before releasing the old views, so their
            // ObjectIdentifiers cannot accidentally be equal through allocator reuse.
            withExtendedLifetime(previous) {
                install(dataKey: previous.dataKey, publishing: previous.publishing,
                        derivedDescription: previous.derivedDescription, value: previous.cell.field.value)
                row.descriptionOverride = previous.descriptionOverride
                row.cell.rowIndexRange = previous.cell.rowIndexRange
            }
        case "focus": window.makeFirstResponder(row.cell.field)
        case "native_insert": nativeInsert(command["text"] as? String ?? "abcd")
        default: break
        }
        record()
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        window = NSWindow(contentRect: NSRect(x: 180, y: 180, width: 460, height: 170),
                          styleMask: [.titled, .closable], backing: .buffered, defer: false)
        window.title = "Row identity probe"
        window.tabbingMode = .disallowed
        other.frame = NSRect(x: 20, y: 25, width: 420, height: 28)
        other.setAccessibilityLabel("Unrelated row input")
        window.contentView!.addSubview(other)
        install(dataKey: "A", publishing: "record", derivedDescription: true, value: "seed")
        window.orderBack(nil)
        window.makeKey()
        window.makeMain()
        window.makeFirstResponder(row.cell.field)
        record()
        _ = Timer.scheduledTimer(withTimeInterval: 0.05, repeats: true) { _ in
            MainActor.assumeIsolated { self.tick() }
        }
    }
}

@main enum RowIdentityProbe {
    @MainActor static func main() {
        let application = NSApplication.shared
        application.setActivationPolicy(.regular)
        let delegate = RowIdentityDelegate()
        application.delegate = delegate
        application.run()
    }
}
