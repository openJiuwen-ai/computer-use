// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices

@main enum NativeKeyboardReceiverReadinessTests {
    enum Stop: Error { case failed }

    static func facts(
        windowRoute: String = "yes", scopeRoute: String = "yes", concrete: Bool = true,
        focusedWindow: Bool = true, focusOwner: Bool = true, locallyFocused: Bool? = true,
        actualPID: Int32? = 91071, presenterPID: Int32 = 91071, nativeWindow: Bool = true
    ) -> NativeKeyboardReceiverReadiness.Facts {
        .init(
            windowRoute: windowRoute, scopeRoute: scopeRoute, concreteFocus: concrete,
            focusedWindowMatches: focusedWindow, focusOwnerMatches: focusOwner,
            locallyFocused: locallyFocused, actualPID: actualPID, presenterPID: presenterPID,
            nativeWindowKnown: nativeWindow)
    }

    static func onlyConfirmedLocalReceiverQualifies() {
        precondition(NativeKeyboardReceiverReadiness.confirmedLocal(facts()))
        let refused = [
            facts(windowRoute: "unknown"), facts(windowRoute: "no"),
            facts(scopeRoute: "unknown"), facts(scopeRoute: "no"),
            facts(concrete: false), facts(focusedWindow: false), facts(focusOwner: false),
            facts(locallyFocused: false), facts(locallyFocused: nil),
            facts(actualPID: nil), facts(actualPID: 91072), facts(nativeWindow: false),
        ]
        for value in refused {
            precondition(!NativeKeyboardReceiverReadiness.confirmedLocal(value),
                         "incomplete or foreign receiver facts cannot replace preparation evidence")
        }
    }

    @MainActor static func windowCommandsKeepTheirPreparation() throws {
        let window = AXUIElementCreateApplication(91071)
        let editor = AXUIElementCreateApplication(91072)
        let other = AXUIElementCreateApplication(91073)
        let state = NativeReceiverSnapshot(
            windowRoute: "yes", focus: editor, ancestors: [], foreground: false, window: window, surface: window,
            localReceiverConfirmed: true)
        let unit = NativeKeyboardUnit(application: .current, window: window, element: editor)
        let insertion = try unit.readiness(kind: .insert, text: "X", state: state, includeDelivery: false)
        precondition(insertion.ready)
        let command = try unit.readiness(kind: .key, key: "super+a", state: state, includeDelivery: false)
        precondition(!command.ready, "a confirmed editor does not prepare AppKit window-command dispatch")
        let paste = try unit.readiness(kind: .paste, text: "X", state: state, includeDelivery: false)
        precondition(!paste.ready)
        let preparedCommand = try unit.readiness(
            kind: .key, key: "super+a", prepared: true, state: state, includeDelivery: false)
        precondition(preparedCommand.ready)
        let changed = NativeReceiverSnapshot(
            windowRoute: "yes", focus: other, ancestors: [], foreground: false, window: window, surface: window)
        let wrongReceiver = try unit.readiness(
            kind: .key, key: "super+a", prepared: true, state: changed, includeDelivery: false)
        precondition(!wrongReceiver.ready, "a prepared window still cannot authorize another focused field")
    }

    @MainActor static func firstKeyDownConsumesBeforeAwait() async throws {
        let gate = Input.FirstKeyDown()
        var trace = [String]()
        try await gate.run {
            trace.append("handoff")
            try await gate.run { trace.append("reentrant-handoff") }
            await Task.yield()
            trace.append("returned")
        }
        trace.append("first-key")
        try await gate.run { trace.append("repeated-handoff") }
        trace.append("remaining-key")
        precondition(trace == ["handoff", "returned", "first-key", "remaining-key"])

        let absent = Input.FirstKeyDown()
        try await absent.run(nil)
        try await absent.run { preconditionFailure("a consumed first-down slot cannot gain a later handoff") }
    }

    @MainActor static func firstKeyFailureDoesNotRetry(cancelled: Bool) async throws {
        let gate = Input.FirstKeyDown()
        var calls = 0
        do {
            try await gate.run {
                calls += 1
                if cancelled { throw CancellationError() }
                throw Stop.failed
            }
            preconditionFailure("handoff failure must propagate before key dispatch")
        } catch {
            precondition(cancelled ? error is CancellationError : error is Stop)
        }
        try await gate.run { calls += 1 }
        precondition(calls == 1, "failed or cancelled first-down preparation must not be retried")
    }

    @MainActor static func main() async throws {
        onlyConfirmedLocalReceiverQualifies()
        try windowCommandsKeepTheirPreparation()
        try await firstKeyDownConsumesBeforeAwait()
        try await firstKeyFailureDoesNotRetry(cancelled: false)
        try await firstKeyFailureDoesNotRetry(cancelled: true)
        print("PASS confirmed local keyboard receiver preserves window-command obligations; first-down handoff is consumed once")
    }
}
