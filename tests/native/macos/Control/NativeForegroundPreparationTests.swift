// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import Foundation

@main enum NativeForegroundPreparationTests {
    @MainActor static func main() throws {
        let target: [String: Any] = ["session": "session", "surface": "window", "node": "button"]
        let intent = try NativeIntent(["kind": "click", "parameters": ["button": "left", "count": 1]], mode: "write")
        let operation = NativeOperation(
            timeoutMs: 30_000, writable: true, intent: intent, target: target, clickContract: "v2", release: {},
            cancelActive: {})
        defer { _ = operation.finish() }
        func expectRefusal(_ expected: FailureCode, _ body: () throws -> Void) {
            do {
                try body()
                preconditionFailure("foreground preparation requires current pre-dispatch evidence")
            } catch let error as DriverError { precondition(error.code == expected) } catch {
                preconditionFailure("unexpected error: \(error)")
            }
        }
        expectRefusal(.invalidRequest) {
            try operation.requireClickForeground(
                target: target, within: [], backgroundFamily: "directed", backgroundUnavailable: false)
        }
        try operation.recordClickBackgroundUnavailable(family: "directed", target: target, within: [], step: 1)
        try operation.requireClickForeground(
            target: target, within: [], backgroundFamily: "directed", backgroundUnavailable: false)
        expectRefusal(.invalidRequest) {
            try operation.requireClickForeground(
                target: ["session": "session", "surface": "another", "node": "button"], within: [],
                backgroundFamily: "directed", backgroundUnavailable: false)
        }
        expectRefusal(.invalidRequest) { try operation.requireForegroundPreparation("keyboard") }
        try operation.recordBackgroundMechanismUnavailable("keyboard")
        try operation.requireForegroundPreparation("keyboard")
        operation.recordInputDispatch()
        expectRefusal(.executionUnknown) {
            try operation.requireClickForeground(
                target: target, within: [], backgroundFamily: "directed", backgroundUnavailable: true)
        }
        expectRefusal(.invalidRequest) { try operation.requireForegroundPreparation("keyboard") }
        let ordinary = NativeActionDocumentPolicy.preparation(
            action: "AXPress", exactStandardWindow: true, otherDocument: false)
        precondition(!ordinary.required, "foreground permission does not request activation of an ordinary action")
        let other = NativeActionDocumentPolicy.preparation(
            action: "AXPress", exactStandardWindow: true, otherDocument: true)
        precondition(other.windowSelectionRequired, "a confirmed other document still requires exact window selection")
        let point = CGPoint(x: 30, y: 40)
        precondition(!ForegroundAssist.canReuseFocusedWindow(
            foreground: true, window: 7, focused: 7, pointerAt: point, hit: { _ in 8 }))
        precondition(!ForegroundAssist.canReuseFocusedWindow(
            foreground: true, window: 7, focused: 7, pointerAt: point, hit: { _ in nil }))
        precondition(ForegroundAssist.canReuseFocusedWindow(
            foreground: true, window: 7, focused: 7, pointerAt: point, hit: { _ in 7 }))
        precondition(ForegroundAssist.canReuseFocusedWindow(
            foreground: true, window: 7, focused: 7, pointerAt: nil, hit: { _ in preconditionFailure() }))
        print("PASS foreground preparation requires original-scope pre-dispatch evidence")
    }
}
