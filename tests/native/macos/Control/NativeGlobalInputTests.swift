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
        try await clickTracking(pointer)
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

    @MainActor static func clickTracking(_ pointer: NativePointerIntent) async throws {
        let point = pointer.start!, source = CGEventSource(stateID: .privateState)!
        let viewport = CGSize(width: 100, height: 100)
        let sequence = try NativePointerEvents(request: pointer, from: point, to: nil, viewport: viewport, source: source)
        let moves = sequence.items.filter { $0.event?.type == .mouseMoved }
        precondition(moves.count == 2 && moves.allSatisfy { $0.point == point } && moves[1].delayMs > 0,
                     "a raw click must send a second same-point move after queued tracking entry")
        var events = [CGEventType]()
        try await sequence.deliver(
            check: { actual, _ in precondition(actual == point) }, post: { event, actual in
                precondition(actual == point)
                events.append(event.type)
            }, canRelease: { true }, skipped: { preconditionFailure("global release was skipped") },
            onDispatch: {}, onPosted: {})
        precondition(events == [.mouseMoved, .mouseMoved, .leftMouseDown, .leftMouseUp])

        events.removeAll()
        var marked = 0, accepted = 0
        do {
            try await sequence.deliver(
                check: { _, _ in if events.count == 2 { throw CancellationError() } },
                post: { event, _ in events.append(event.type) }, canRelease: { true },
                skipped: { preconditionFailure("no button was held") },
                onDispatch: { marked += 1 }, onPosted: { accepted += 1 })
            preconditionFailure("cancellation before down was ignored")
        } catch is CancellationError {}
        precondition(events == [.mouseMoved, .mouseMoved] && marked == 2 && accepted == 2,
                     "cancellation retains the two posted moves without sending down or replaying input")

        let directed = try NativePointerIntent(
            unit: "pointer_sequence", kind: "click", parameters: ["x": 10, "y": 20], foreground: false)
        let scroll = try NativePointerIntent(
            unit: "pointer_sequence", kind: "scroll",
            parameters: ["x": -740, "y": 26, "direction": "down", "pages": 1], foreground: false, screen: true)
        let drag = try NativePointerIntent(
            unit: "pointer_sequence", kind: "drag",
            parameters: ["fromX": -740, "fromY": 26, "toX": -710, "toY": 40], foreground: false, screen: true)
        for request in [directed, scroll, drag] {
            let adjacent = try NativePointerEvents(
                request: request, from: request.start!, to: request.end, viewport: viewport, source: source)
            precondition(adjacent.items.filter { $0.event?.type == .mouseMoved }.count == 1)
        }
    }
}
