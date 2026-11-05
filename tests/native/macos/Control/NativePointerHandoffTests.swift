// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices

/// Exercises the actual event train without posting anything to an application.
/// A delayed foreground excursion can appear after entry/move; the handoff must
/// finish before the still-unsent first down and cannot replay the earlier events.
@main enum NativePointerHandoffTests {
    enum Stop: Error { case handoff, changedReceiver }
    enum FailureMode { case handoff, cancelled, deadline, changedReceiver }

    @MainActor static func makeSequence(kind: String = "click", button: String = "left", count: Int = 1) throws
        -> NativePointerEvents
    {
        let parameters: [String: Any]
        switch kind {
        case "drag": parameters = ["fromX": 10, "fromY": 20, "toX": 40, "toY": 50]
        case "scroll": parameters = ["x": 10, "y": 20, "direction": "down", "pages": 1]
        default: parameters = ["x": 10, "y": 20, "button": button, "count": count]
        }
        let request = try NativePointerIntent(
            unit: "pointer_sequence", kind: kind, parameters: parameters, foreground: false)
        return try NativePointerEvents(
            request: request, from: CGPoint(x: 10, y: 20), to: request.end,
            viewport: CGSize(width: 100, height: 100), source: CGEventSource(stateID: .privateState)!)
    }

    @MainActor static func entry() -> CGEvent {
        NSEvent.enterExitEvent(
            with: .mouseEntered, location: .zero, modifierFlags: [], timestamp: 0,
            windowNumber: 71, context: nil, eventNumber: 0, trackingNumber: 0, userData: nil)!.cgEvent!
    }

    @MainActor static func handoffPrecedesFreshValidation(button: String, count: Int) async throws {
        let sequence = try makeSequence(button: button, count: count)
        let entered = entry()
        let down: CGEventType = button == "right" ? .rightMouseDown : button == "middle" ? .otherMouseDown : .leftMouseDown
        let up: CGEventType = button == "right" ? .rightMouseUp : button == "middle" ? .otherMouseUp : .leftMouseUp
        var types = [CGEventType](), trace = [String](), handoffs = 0
        var returnedForeground = false, checkedReturnedReceiver = false
        try await sequence.deliver(
            entry: entered,
            check: { point, held in
                precondition(point == CGPoint(x: 10, y: 20))
                trace.append("validate")
                if types.count >= 2 && !types.contains(down) {
                    precondition(!held && returnedForeground,
                                 "the first-down validation must consume the post-handoff state")
                    checkedReturnedReceiver = true
                }
            }, post: { event, _ in
                if event.type == down {
                    precondition(returnedForeground && checkedReturnedReceiver,
                                 "never press before awaited handoff and fresh receiver validation")
                }
                types.append(event.type)
                trace.append("post:\(event.type.rawValue)")
            }, canRelease: { true }, skipped: { preconditionFailure("paired release must remain available") },
            onDispatch: {}, onPosted: {}, beforeFirstMouseDown: { point in
                handoffs += 1
                precondition(point == CGPoint(x: 10, y: 20))
                precondition(types == [entered.type, .mouseMoved],
                             "handoff runs after entry/move, before any requested down")
                trace.append("handoff")
                await Task.yield()
                returnedForeground = true
                trace.append("handoffReturned")
            })
        precondition(handoffs == 1, "double click must not reacquire or return foreground between its clicks")
        let expected = [entered.type, .mouseMoved] + Array(repeating: [down, up], count: count).flatMap { $0 }
        precondition(types == expected, "entry/move and requested click events must not be replayed")
        let returned = trace.firstIndex(of: "handoffReturned")!
        precondition(trace[returned + 1] == "validate", "preparation changes require a fresh check before down")
    }

    @MainActor static func handoffFailureDoesNotPress(_ mode: FailureMode) async throws {
        let sequence = try makeSequence(count: 2)
        let entered = entry()
        var types = [CGEventType](), handoffs = 0, changedReceiver = false
        var failure: Error?
        do {
            try await sequence.deliver(
                entry: entered, check: { _, _ in if changedReceiver { throw Stop.changedReceiver } },
                post: { event, _ in types.append(event.type) }, canRelease: { true },
                skipped: { preconditionFailure("no paired input was pressed") }, onDispatch: {}, onPosted: {},
                beforeFirstMouseDown: { _ in
                    handoffs += 1
                    precondition(types == [entered.type, .mouseMoved])
                    switch mode {
                    case .handoff: throw Stop.handoff
                    case .cancelled: throw CancellationError()
                    case .deadline: throw RequestDeadlineExceeded()
                    case .changedReceiver: changedReceiver = true
                    }
                })
        } catch { failure = error }
        precondition(failure != nil && handoffs == 1)
        switch mode {
        case .cancelled: precondition(failure is CancellationError)
        case .deadline: precondition(failure is RequestDeadlineExceeded)
        case .handoff, .changedReceiver: precondition(failure is Stop)
        }
        precondition(types == [entered.type, .mouseMoved],
                     "failed preparation or its changed receiver stops before down, preserving earlier effects")
    }

    @MainActor static func unrelatedGesturesDoNotHandoff(_ kind: String) async throws {
        let sequence = try makeSequence(kind: kind)
        let entered = entry()
        var types = [CGEventType](), handoffs = 0
        try await sequence.deliver(
            entry: entered, check: { _, _ in }, post: { event, _ in types.append(event.type) },
            canRelease: { true }, skipped: { preconditionFailure("original release must remain available") },
            onDispatch: {}, onPosted: {}, beforeFirstMouseDown: { _ in handoffs += 1 })
        precondition(handoffs == 0, "only click may use the optional before-first-down handoff")
        precondition(types.prefix(2) == [entered.type, .mouseMoved])
        if kind == "drag" {
            precondition(types.filter { $0 == .leftMouseDown }.count == 1 && types.last == .leftMouseUp)
        } else {
            precondition(types == [entered.type, .mouseMoved, .scrollWheel])
        }
    }

    @MainActor static func absentHandoffPreservesOriginalSequence() async throws {
        let sequence = try makeSequence()
        let entered = entry()
        var types = [CGEventType]()
        try await sequence.deliver(
            entry: entered, check: { _, _ in }, post: { event, _ in types.append(event.type) },
            canRelease: { true }, skipped: { preconditionFailure("paired release must remain available") },
            onDispatch: {}, onPosted: {})
        precondition(types == [entered.type, .mouseMoved, .leftMouseDown, .leftMouseUp])
    }

    @MainActor static func main() async throws {
        for button in ["left", "right", "middle"] {
            for count in [1, 2] { try await handoffPrecedesFreshValidation(button: button, count: count) }
        }
        for mode in [FailureMode.handoff, .cancelled, .deadline, .changedReceiver] {
            try await handoffFailureDoesNotPress(mode)
        }
        for kind in ["drag", "scroll"] { try await unrelatedGesturesDoNotHandoff(kind) }
        try await absentHandoffPreservesOriginalSequence()
        print("PASS first-down handoff is awaited once; fresh receiver checks precede click without replaying movement or changing drag/scroll")
    }
}
