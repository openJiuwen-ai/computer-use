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
            action: "AXPress", exactStandardWindow: true, otherDocument: false, confirmedWebArea: false,
            foreground: false)
        precondition(!ordinary.required, "foreground permission does not request activation of an ordinary action")
        let web = NativeActionDocumentPolicy.preparation(
            action: "AXPress", exactStandardWindow: true, otherDocument: false, confirmedWebArea: true,
            foreground: false)
        precondition(web.foregroundOnly, "a proven focus-gated action retains its actual preparation requirement")
        let previous = NativeClickContract(
            routeFamily: "web", delivery: .objectAction, admission: .preparationRequired, reason: nil,
            preparationOptions: ["foreground"], backgroundUnavailable: true, documentForeground: .required(ready: false)
        )
        let refreshed = NativeClickContract(
            routeFamily: "web", delivery: .objectAction, admission: .ready, reason: nil, preparationOptions: [],
            documentForeground: .unconfirmed(ready: false)
        ).retainingDocumentForeground(from: previous)
        precondition(refreshed.backgroundUnavailable && refreshed.preparationOptions == ["foreground"])
        print("PASS foreground preparation requires original-scope pre-dispatch evidence")
    }
}
