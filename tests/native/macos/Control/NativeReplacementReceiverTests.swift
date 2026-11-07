// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices

@main enum NativeReplacementReceiverTests {
    @MainActor static func main() async throws {
        try keyboardTextEntryUsesReadProtocol()
        try await restoreOnlyProvedCollapsedRange()
        try refocusOnlyProvedOriginalForegroundWindow()
        scalarRecipeRequiresTextCapability()
        richEditorSelectionUsesScopedCommand()
        try richTextFragmentsPreserveInput()
        let window = AXUIElementCreateApplication(1_000_001)
        let element = AXUIElementCreateApplication(1_000_002)
        let other = AXUIElementCreateApplication(1_000_003)
        let unit = NativeKeyboardUnit(application: .current, window: window, element: element)
        let state = NativeReceiverSnapshot(windowRoute: "yes", focus: element, ancestors: [], foreground: true)
        let request = try NativeKeyboardIntent(
            unit: "key_sequence", kind: "replace", parameters: ["text": "replacement"], foreground: false)
        var selectionReads = 0
        let sample: (AXUIElement, NativeReplacementUnit.Selection) -> NativeReplacementUnit.Snapshot? = { _, _ in
            selectionReads += 1
            return nil
        }
        let admission = try unit.readiness(
            kind: .replace, replacement: .range, state: state, includeDelivery: false, replacementSnapshot: sample)
        precondition(admission.pending.contains("selection") && selectionReads == 1)
        for _ in 0..<8 {
            let receiver = try unit.receiverReadiness(
                request, replacement: .range, prepared: true, state: state, replacementSnapshot: sample)
            precondition(
                receiver.ready && selectionReads == 1,
                "fragment validation repeated the replacement owner's selection query")
        }
        let changed = NativeReceiverSnapshot(windowRoute: "yes", focus: other, ancestors: [], foreground: true)
        let refused = try unit.receiverReadiness(
            request, replacement: .range, prepared: true, state: changed, replacementSnapshot: sample)
        precondition(
            !refused.ready && refused.pending.contains("receiver") && selectionReads == 1,
            "omitting redundant selection queries must retain receiver validation")

        let source = CGEventSource(stateID: .privateState)!
        func payload(_ event: CGEvent) -> String {
            var count = 0
            event.keyboardGetUnicodeString(maxStringLength: 0, actualStringLength: &count, unicodeString: nil)
            var units = [UniChar](repeating: 0, count: count)
            event.keyboardGetUnicodeString(
                maxStringLength: units.count, actualStringLength: &count, unicodeString: &units)
            return String(utf16CodeUnits: units, count: count)
        }
        for value in ["2026/11/13", "e\u{301}😀", String(repeating: "1", count: 256)] {
            let events = try Input.atomicTextEvents(value, source: source, key: (19, []))!
            precondition(events.down.type == .keyDown && events.up.type == .keyUp)
            precondition(
                payload(events.down) == value && payload(events.up) == value,
                "whole replacement payload was split, truncated, or lost a surrogate")
            precondition(events.down.getIntegerValueField(.keyboardEventKeycode) == 19)
        }
        let foreground = try Input.atomicTextEvents("A", source: source, key: (0, .maskShift), foreground: true)!
        precondition(
            foreground.down.flags == .maskShift && foreground.up.flags.isEmpty,
            "whole text must retain the existing foreground modifier-release convention")
        let scalar = try Input.unicodeEvents("😀", source: source)
        precondition(
            payload(scalar.0) == "😀" && payload(scalar.1) == "😀",
            "ordinary scalar insertion must retain its original event payload")
        let empty = try Input.atomicTextEvents("", source: source)
        precondition(empty == nil)
        // Quartz's accepted size can vary by platform. A large payload either
        // remains complete or removes this recipe before selection changes.
        let largeText = String(repeating: "1", count: 65_536)
        if let large = try Input.atomicTextEvents(largeText, source: source) {
            precondition(
                payload(large.down) == largeText && payload(large.up) == largeText,
                "lossy whole-string payload must not reach selection or dispatch")
        }
        do {
            _ = try Input.atomicTextEvents("date\n", source: source)
            preconditionFailure("whole-string encoding silently introduced a submit/control key")
        } catch let error as DriverError { precondition(error.code == .invalidRequest) }
        print("PASS replacement receiver, scalar text contract and atomic encoding regressions")
    }

    @MainActor static func keyboardTextEntryUsesReadProtocol() throws {
        let window = AXUIElementCreateApplication(1_000_001)
        let element = AXUIElementCreateApplication(1_000_002)
        let other = AXUIElementCreateApplication(1_000_003)
        var rawRange = CFRange(location: 1, length: 2)
        let range = AXValueCreate(.cfRange, &rawRange)!
        var fields: [String: AXRead<CFTypeRef>] = [
            kAXValueAttribute: .value("a😀b" as CFString),
            kAXSelectedTextRangeAttribute: .value(range),
        ]
        func read(_ name: String) -> AXRead<CFTypeRef> { fields[name] ?? .unsupported }
        func entry(_ role: String, enabled: AXRead<CFTypeRef> = .unsupported) -> NativeKeyboardTextEntry? {
            NativeKeyboardTextEntry.read(role: role, enabled: enabled, field: read)
        }
        let original = entry(kAXTextFieldRole)!
        let selection = original.selection(read)!
        precondition(selection == .init(count: 4, location: 1, length: 2),
                     "keyboard replacement eligibility is independent of AX setters")
        precondition(entry(kAXTextFieldRole, enabled: .value(kCFBooleanFalse)) == nil)
        fields["AXReadOnly"] = .value(kCFBooleanTrue)
        precondition(entry(kAXTextFieldRole) == nil)
        fields.removeValue(forKey: "AXReadOnly")
        fields[kAXSubroleAttribute] = .value(kAXSecureTextFieldSubrole as CFString)
        precondition(entry(kAXTextFieldRole) == nil)
        fields.removeValue(forKey: kAXSubroleAttribute)
        precondition(entry(kAXIncrementorRole) == nil,
                     "structured scalar replacement keeps its separate recipe")
        precondition(entry(kAXComboBoxRole)?.selection(read) == nil,
                     "a numeric-only ValueInterface cannot qualify as a text editor")
        fields[kAXValueAttribute] = .unsupported
        fields[kAXNumberOfCharactersAttribute] = .value(NSNumber(value: 4))
        fields[kAXSelectedTextAttribute] = .value("😀" as CFString)
        precondition(entry(kAXComboBoxRole)?.selection(read) == selection,
                     "a composite qualifies through the owner's own readable extent and range")
        fields[kAXSelectedTextAttribute] = .value("x" as CFString)
        precondition(entry(kAXComboBoxRole)?.selection(read) == nil)

        let unit = NativeKeyboardUnit(application: .current, window: window, element: element)
        var state = NativeReceiverSnapshot(windowRoute: "yes", focus: element, ancestors: [], foreground: true)
        state.window = window
        state.rawFocus = element
        func readiness() throws -> NativeReadiness {
            try unit.readiness(
                kind: .replace, replacement: .keyboardSelectAll(original), state: state,
                includeDelivery: false, replacementSnapshot: { _, _ in .numeric(selection) })
        }
        let confirmed = try readiness()
        precondition(confirmed.ready)
        state.rawFocus = other
        let rejected = try readiness()
        precondition(!rejected.ready && rejected.pending.contains("receiver"),
                     "the original text-entry proof cannot follow another focus object")
        state.rawFocus = element
        state.window = other
        state.rawFocusOwner = other
        let changedWindow = try readiness()
        precondition(!changedWindow.ready)
        state.surface = window
        let normalizedWindow = try readiness()
        precondition(normalizedWindow.ready, "a validated surface can resolve its original keyboard window")
    }

    @MainActor static func restoreOnlyProvedCollapsedRange() async throws {
        let text = Array("abcd".utf16)
        let lost = NativeTextSelection(count: 4, location: 3, length: 0)
        for full in [true, false] {
            for current in [text, Array("ABCD".utf16)] {
                var restored = [Int]()
                var sampled = [String]()
                let didRestore = try await NativeReplacementUnit.restoreCollapsedRange(
                    original: text, fullObserved: full,
                    readText: { sampled.append("text"); return current },
                    readRange: { sampled.append("range"); return lost },
                    validateReceiver: { sampled.append("receiver") },
                    restore: { restored.append($0) })
                if full && current == text {
                    precondition(didRestore && restored == [4] && sampled == ["range", "text", "receiver", "text", "range"])
                } else {
                    precondition(!didRestore && restored.isEmpty)
                }
            }
        }
        var samples = [lost, NativeTextSelection(count: 4, location: 0, length: 2)]
        let moved = try await NativeReplacementUnit.restoreCollapsedRange(
            original: text, fullObserved: true, readText: { text }, readRange: { samples.removeFirst() },
            validateReceiver: {},
            restore: { _ in preconditionFailure("a changed range must not be overwritten") })
        precondition(!moved)
        var current = text
        let changedDuringValidation = try await NativeReplacementUnit.restoreCollapsedRange(
            original: text, fullObserved: true, readText: { current }, readRange: { lost },
            validateReceiver: { current = Array("ABCD".utf16) },
            restore: { _ in preconditionFailure("text changed while receiver was sampled") })
        precondition(!changedDuringValidation)
    }

    @MainActor static func refocusOnlyProvedOriginalForegroundWindow() throws {
        let window = AXUIElementCreateApplication(1_000_001)
        let original = AXUIElementCreateApplication(1_000_002)
        let other = AXUIElementCreateApplication(1_000_003)
        func sample(foreground: Bool = true, reported: AXUIElement? = nil) -> NativeReceiverSnapshot {
            .init(windowRoute: "yes", focus: window, ancestors: [], foreground: foreground, window: window,
                  reportedFocus: reported ?? window, rawFocus: reported ?? window, rawFocusOwner: window)
        }
        func windowOnly(_ state: NativeReceiverSnapshot) -> Bool {
            NativeReplacementWindowReceiver.isWindowOnly(state, original: original, window: window)
        }
        var steps = [String]()
        var state = sample()
        let recovery = NativeReplacementWindowReceiver(
            windowOnly: { steps.append("receiver"); return windowOnly(state) },
            canSetFocus: { steps.append("permission"); return true },
            focusOriginal: { steps.append("focus original") }, desktopUnchanged: { true })
        let prepared = try recovery.prepare(ifUnchanged: { steps.append("literal"); return true })
        precondition(prepared && steps == ["receiver", "permission", "literal", "receiver", "focus original"])
        steps.removeAll()
        let changedLiteral = try recovery.prepare(ifUnchanged: { false })
        precondition(!changedLiteral && !steps.contains("focus original"))
        steps.removeAll()
        let changedDuringRead = try recovery.prepare(ifUnchanged: {
            state = sample(reported: other)
            return true
        })
        precondition(!changedDuringRead && !steps.contains("focus original"))
        state = sample(foreground: false)
        let background = try recovery.prepare(ifUnchanged: {
            preconditionFailure("background Window-only loss does not authorize recovery")
        })
        precondition(!background)
        state = sample()
        state.reportedFocus = nil
        precondition(!windowOnly(state), "missing provider focus is not proved Window-only loss")
        state = sample()
        state.scopeRoute = "no"
        precondition(!windowOnly(state), "a competing modal or surface remains a strict boundary")
    }

    static func richEditorSelectionUsesScopedCommand() {
        func marker(_ value: UInt8) -> AXTextMarker {
            var bytes = [UInt8](repeating: value, count: 32)
            return AXTextMarkerCreate(nil, &bytes, bytes.count)
        }
        let start = marker(1), end = marker(2)
        let caret = AXTextMarkerRangeCreate(nil, start, start)
        let selected = AXTextMarkerRangeCreate(nil, start, end)
        func scoped(_ state: NativeTextSelection, range: AXTextMarkerRange, owns: Bool = true) -> Bool {
            NativeKeyboardSelection.isScoped(numeric: { state }, selected: { .value(range) }, owns: { _ in owns })
        }
        // A CSS placeholder is exposed as 23 characters although the editable
        // region is empty. Select All legitimately leaves a caret after it.
        precondition(scoped(.init(count: 23, location: 23, length: 0), range: caret))
        // Non-editable leading content can precede the actual editing range.
        precondition(scoped(.init(count: 30, location: 23, length: 7), range: selected))
        precondition(scoped(.init(count: 7, location: 0, length: 7), range: selected))
        precondition(!scoped(.init(count: 23, location: 23, length: 0), range: caret, owns: false))
        precondition(!scoped(.init(count: 7, location: 0, length: 7, contiguous: false), range: selected))
        precondition(!scoped(.init(count: 7, location: nil, length: nil), range: selected))
        var reads = 0
        precondition(!NativeKeyboardSelection.isScoped(
            numeric: {
                reads += 1
                return .init(count: 30, location: reads == 1 ? 23 : 24, length: reads == 1 ? 7 : 6)
            }, selected: { .value(selected) }, owns: { _ in true }))
        reads = 0
        precondition(!NativeKeyboardSelection.isScoped(
            numeric: { .init(count: 30, location: 23, length: 7) },
            selected: {
                reads += 1
                return .value(reads == 1 ? selected : caret)
            }, owns: { _ in true }))
    }

    @MainActor static func richTextFragmentsPreserveInput() throws {
        let text = String(repeating: "a", count: 19) + "🙂中文 e\u{301} " + String(repeating: "test🙂", count: 30)
        let fragments = try Input.prepareTextFragments(text, foreground: true)!
        precondition(fragments.count > 1)
        var complete = ""
        for fragment in fragments {
            var pair = [String]()
            for event in [fragment.down, fragment.up] {
                var count = 0
                event.keyboardGetUnicodeString(maxStringLength: 0, actualStringLength: &count, unicodeString: nil)
                precondition(count > 0 && count <= 20)
                var units = [UniChar](repeating: 0, count: count)
                event.keyboardGetUnicodeString(maxStringLength: count, actualStringLength: &count, unicodeString: &units)
                let value = String(decoding: units, as: UTF16.self)
                precondition(Array(value.utf16) == units, "a Unicode scalar was split between fragments")
                pair.append(value)
            }
            precondition(pair[0] == pair[1])
            complete += pair[0]
        }
        precondition(complete == text, "fragmentation must retain every original character exactly once")
        let empty = try Input.prepareTextFragments("", foreground: false)
        precondition(empty?.isEmpty == true)
    }

    @MainActor static func scalarRecipeRequiresTextCapability() {
        func eligible(
            role: String? = kAXIncrementorRole, selection: NativeReplacementUnit.Selection = .shortcut,
            count: AXRead<CFTypeRef> = .value(NSNumber(value: 10)), writable: Bool = true,
            before: NativeTextSelection = .init(count: 10, location: 0, length: 4)
        ) -> Bool {
            NativeReplacementUnit.usesScalarTextRecipe(
                selection: selection, role: role, characterCount: count, rangeWritable: writable,
                before: .numeric(before))
        }
        // The observed Qt QDateEdit exposes this text contract even when all
        // QObject names are custom. No identifier/class-name input is needed.
        precondition(eligible())
        precondition(eligible(before: .init(count: 10, location: 8, length: 2)))
        precondition(eligible(before: .init(count: 10, location: 0, length: 10)))

        // Qt Quick's standard SpinBox has a ValueInterface, not TextInterface.
        // A value string fallback in the ordinary selection reader cannot
        // manufacture the missing native character-count evidence here.
        precondition(!eligible(count: .unsupported))
        precondition(!eligible(count: .unavailable(.noValue)))
        precondition(!eligible(count: .value("2026/11/15" as CFString)))
        precondition(!eligible(count: .value(kCFBooleanTrue)))
        precondition(!eligible(count: .value(NSNumber(value: -1))))
        precondition(!eligible(count: .value(NSNumber(value: 9))))
        precondition(!eligible(writable: false))
        precondition(!eligible(before: .init(count: 10, location: nil, length: nil)))
        precondition(!eligible(before: .init(count: 10, location: 0, length: 4, contiguous: false)))
        for role: String? in [nil, kAXTextFieldRole, kAXTextAreaRole, "AXWebArea", kAXSliderRole] {
            precondition(!eligible(role: role), "ordinary text and non-text controls keep their existing recipe")
        }
        for selection: NativeReplacementUnit.Selection in [.range, .markers, .selectAll] {
            precondition(!eligible(selection: selection))
        }
    }
}
