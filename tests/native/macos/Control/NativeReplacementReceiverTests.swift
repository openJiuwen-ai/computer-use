// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices

@main enum NativeReplacementReceiverTests {
    @MainActor static func main() throws {
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
    }
}
