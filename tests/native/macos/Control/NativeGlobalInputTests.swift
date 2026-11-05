// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import Foundation
import CoreGraphics

/// Protocol and event-recipe checks only: no OS input is posted.
@main enum NativeGlobalInputTests {
    @MainActor static func main() async throws {
        let scope: [String: Any] = ["session": "global-test", "global": true]
        func begin(_ target: [String: Any], kind: String = "click", global: Bool = true,
                   clickContract: String? = nil) throws -> NativeBeginRequest {
            var request: [String: Any] = [
                "mode": "write", "target": target,
                "intent": ["kind": kind, "parameters": ["x": -740, "y": 26], "globalInput": global]
            ]
            if let clickContract { request["clickContract"] = clickContract }
            return try NativeBeginRequest(request)
        }
        let global = try begin(scope)
        precondition(global.intent.globalInput && global.clickContract == nil)
        let command = try NativeUnitCommand([
            "kind": "global_input", "primitiveId": "global", "target": scope, "foreground": false
        ])
        precondition(command.target.count == 2 && command.target["capture"] == nil)
        let object: [String: Any] = [
            "session": "global-test", "instance": NSNull(), "rootWindow": NSNull(),
            "surface": NSNull(), "node": NSNull(), "capture": NSNull()
        ]
        func rejects(_ body: () throws -> Void) {
            do { try body(); preconditionFailure("invalid global/object protocol accepted") }
            catch let error as DriverError { precondition(error.code == .invalidRequest) }
            catch { preconditionFailure("unexpected error: \(error)") }
        }
        rejects { _ = try begin(object) }
        rejects { _ = try begin(scope, global: false) }
        rejects { _ = try begin(scope, kind: "replace") }
        rejects { _ = try begin(scope, clickContract: "v2") }
        rejects { _ = try NativeGlobalTarget(["session": "global-test", "global": true, "capture": "old"]) }
        rejects {
            _ = try NativeUnitCommand([
                "kind": "pointer_sequence", "primitiveId": "old", "target": scope, "foreground": false
            ])
        }
        rejects {
            _ = try NativeUnitCommand([
                "kind": "global_input", "primitiveId": "global", "target": object, "foreground": false
            ])
        }
        rejects {
            _ = try NativeUnitCommand([
                "kind": "global_input", "primitiveId": "global", "target": scope, "foreground": true
            ])
        }
        let pointer = try NativePointerIntent(
            unit: "pointer_sequence", kind: "click", parameters: global.intent.parameters,
            foreground: false, screen: true)
        precondition(pointer.start == CGPoint(x: -740, y: 26))
        rejects {
            _ = try NativePointerIntent(
                unit: "pointer_sequence", kind: "click", parameters: global.intent.parameters, foreground: false)
        }
        let operation = NativeOperation(
            timeoutMs: 5000, writable: true, intent: global.intent, target: scope, release: {}, cancelActive: {})
        defer { _ = operation.finish() }
        var calls = 0
        let result = try await operation.execute(step: 1, payload: Data([1])) {
            calls += 1
            precondition(NativeForegroundResponsibility.current == nil,
                         "raw input must never inherit automatic foreground preparation/restoration")
            operation.recordInputDispatch()
            return ["dispatch": "dispatched", "cleanup": "complete"]
        }
        let replay = try await operation.execute(step: 1, payload: Data([1])) {
            preconditionFailure("duplicate raw input must not be posted twice")
        }
        precondition(calls == 1 && NSDictionary(dictionary: result).isEqual(to: replay))
        try await NativeInputAuthorization.$globalInputRequested.withValue(true) {
            try await NativeInputAuthorization.$globalInputAllowed.withValue(true) {
                for kind in ["insert", "paste"] {
                    let intent = try NativeIntent(
                        ["kind": kind, "parameters": ["text": ""], "globalInput": true], mode: "write")
                    let empty = NativeOperation(
                        timeoutMs: 1000, writable: true, intent: intent, target: scope, release: {}, cancelActive: {})
                    defer { _ = empty.finish() }
                    let receipt = NativeExecutionReceipt(target: scope, onEffect: {
                        preconditionFailure("empty input must have no clipboard/preparation effect")
                    })
                    try await NativeGlobalInputUnit.run(empty, receipt: receipt)
                    precondition(receipt.succeeded && receipt.dispatch == "dispatched" && receipt.acceptedCount == 0)
                }
            }
        }
        print("PASS explicit global scope, no object contract, negative screen coordinates, no foreground owner, dedup, empty input")
    }
}
