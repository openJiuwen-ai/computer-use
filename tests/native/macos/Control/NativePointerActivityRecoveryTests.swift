// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices

@main enum NativePointerActivityRecoveryTests {
    enum Stop: Error { case scope, geometry, receiver }

    static func proof(
        kind: NativePointerIntent.Kind = .click, delivery: NativeClickDelivery = .windowDirected,
        requirement: NativePointerReadiness.Requirement = .contentReceiver, remote: Bool = false,
        initialReady: Bool = true, initialFocus: NativePointerReadiness.WindowFocus = .target,
        initialForeground: Bool = true, initialPrepared: Bool? = nil,
        currentReady: Bool = false, currentForeground: Bool = false,
        windowKind: NativePointerReadiness.WindowKind = .keyRequiring,
        currentFocus: NativePointerReadiness.WindowFocus = .absent, active: Bool? = false
    ) -> NativePointerActivityRecovery.Missing? {
        NativePointerActivityRecovery.missing(
            window: 71, kind: kind, delivery: delivery, requirement: requirement, remote: remote,
            initialReady: initialReady, initialFocus: initialFocus, initialForeground: initialForeground,
            initialPrepared: initialPrepared, currentReady: currentReady, currentForeground: currentForeground,
            windowKind: windowKind, currentFocus: currentFocus, currentActive: active)
    }

    static func qualification() {
        precondition(proof() != nil && proof(currentFocus: .target) != nil)
        precondition(proof(initialForeground: false, initialPrepared: true) != nil)
        for value in [
            proof(kind: .scroll), proof(kind: .drag), proof(delivery: .objectAction),
            proof(requirement: .foregroundWindowPoint), proof(remote: true), proof(initialReady: false),
            proof(initialFocus: .absent), proof(initialFocus: .otherWindow), proof(initialFocus: .failed),
            proof(initialForeground: false, initialPrepared: false), proof(currentReady: true),
            proof(currentForeground: true), proof(windowKind: .identityConflict), proof(windowKind: .visualOnly),
            proof(windowKind: .otherDeclared), proof(currentFocus: .otherWindow), proof(currentFocus: .failed),
            proof(currentFocus: .unresolved), proof(active: true), proof(active: nil)
        ] { precondition(value == nil, "only known local activity loss may qualify this frozen addressed click") }
    }

    @MainActor static func originalChecksPrecedeMarker() throws {
        for stop in [Stop.scope, Stop.geometry, Stop.receiver] {
            var markers = 0
            do {
                try ValidateNativePointerEventPass.run(
                    kind: .click, continuingDrag: false, guardOperation: {},
                    checkTarget: { if stop == .scope { throw stop } },
                    queryRecipient: { if stop != .scope { throw stop } }, queryReadiness: { false },
                    checkPhysicalInput: {}, unavailable: { markers += 1; return proof()! })
                preconditionFailure("original boundary conflict was swallowed")
            } catch is Stop {}
            precondition(markers == 0)
        }
    }

    @MainActor static func sequence(_ mode: String, kind: String = "click", count: Int = 1) async throws {
        let source = CGEventSource(stateID: .privateState)!
        let parameters: [String: Any] = kind == "drag"
            ? ["fromX": 10, "fromY": 20, "toX": 40, "toY": 50]
            : kind == "scroll" ? ["x": 10, "y": 20, "direction": "down", "pages": 1]
            : ["x": 10, "y": 20, "count": count]
        let request = try NativePointerIntent(unit: "pointer_sequence", kind: kind, parameters: parameters, foreground: false)
        let sequence = try NativePointerEvents(
            request: request, from: CGPoint(x: 10, y: 20), to: request.end,
            viewport: CGSize(width: 100, height: 100), source: source)
        let entry = NSEvent.enterExitEvent(
            with: .mouseEntered, location: .zero, modifierFlags: [], timestamp: 0,
            windowNumber: 71, context: nil, eventNumber: 0, trackingNumber: 0, userData: nil)!.cgEvent!
        var types = [CGEventType](), recoveries = 0, checks = 0
        var lost = false, boundary: Stop?
        var failure: Error?
        let missing = proof()!
        do {
            try await sequence.deliver(
                entry: entry,
                check: { _, _ in
                    checks += 1
                    if let boundary { throw boundary }
                    if lost {
                        if mode == "untyped" { throw DriverError("background pointer route changed", code: .inputUnavailable) }
                        throw missing
                    }
                },
                post: { event, _ in
                    types.append(event.type)
                    if mode == "afterDown" {
                        if event.type == .leftMouseDown { lost = true }
                    } else if event.type == .mouseMoved { lost = true }
                }, canRelease: { true }, skipped: { preconditionFailure("paired release must not be skipped") },
                onDispatch: {}, onPosted: {}, recoverLocalActivity: { _, value in
                    recoveries += 1
                    precondition(value.window == 71 && !types.contains(.leftMouseDown), "no down outcome can be replayed")
                    precondition(types.count == 2 && types[0] == entry.type && types[1] == .mouseMoved)
                    switch mode {
                    case "cancel": throw CancellationError()
                    case "deadline": throw RequestDeadlineExceeded()
                    case "pending": throw DriverError("foreground return still pending", code: .executionUnknown, effect: .possible)
                    case "scope": boundary = .scope
                    case "geometry": boundary = .geometry
                    case "receiver": boundary = .receiver
                    default: break
                    }
                    lost = mode == "ignored"
                })
        } catch { failure = error }
        if mode == "success" {
            precondition(failure == nil && recoveries == 1)
            precondition(types == [entry.type, .mouseMoved, .leftMouseDown, .leftMouseUp],
                         "resume the current event: enter/move once, requested down/up exactly once")
        } else if mode == "afterDown" {
            precondition(failure is NativePointerActivityRecovery.Missing && recoveries == 0)
            precondition(types == [entry.type, .mouseMoved, .leftMouseDown, .leftMouseUp],
                         "a later click cannot repair or replay an already attempted down")
        } else {
            precondition(failure != nil)
            let permitted = kind == "click" && mode != "untyped"
            precondition(recoveries == (permitted ? 1 : 0))
            precondition(types == [entry.type, .mouseMoved], "failed recovery preserves earlier movement without sending down/up")
        }
        precondition(checks >= 3)
    }

    @MainActor static func main() async throws {
        qualification()
        try originalChecksPrecedeMarker()
        for mode in ["success", "ignored", "cancel", "deadline", "pending", "scope", "geometry", "receiver", "untyped"] {
            try await sequence(mode)
        }
        try await sequence("afterDown", count: 2)
        for kind in ["drag", "scroll"] { try await sequence("excluded", kind: kind) }
        print("PASS one local-activity repair before first down; frozen event train, scope/geometry/receiver and paired cleanup remain authoritative")
    }
}
