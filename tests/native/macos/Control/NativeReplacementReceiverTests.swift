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
            precondition(receiver.ready && selectionReads == 1,
                         "fragment validation repeated the replacement owner's selection query")
        }
        let changed = NativeReceiverSnapshot(windowRoute: "yes", focus: other, ancestors: [], foreground: true)
        let refused = try unit.receiverReadiness(
            request, replacement: .range, prepared: true, state: changed, replacementSnapshot: sample)
        precondition(!refused.ready && refused.pending.contains("receiver") && selectionReads == 1,
                     "omitting redundant selection queries must retain receiver validation")
    }
}
