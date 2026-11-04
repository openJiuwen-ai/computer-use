// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices

/// Owns only foreground changes caused by a background preparation. It ends
/// before requested input: restoring after a click can dismiss its new popup.
/// No requested input, retry selection or foreground-dependent route lives here.
@MainActor final class NativeBackgroundPreparationScope {
    @TaskLocal static var current: NativeBackgroundPreparationScope?

    enum Outcome: String { case notNeeded, restored, ownershipLost, retained, unavailable, failed, unknown }
    struct State {
        var foregroundPID: pid_t?
        var originalCurrent: Bool
        var targetCurrent: Bool
        var activity: [UInt32]
        var externalActivation: Bool
    }

    let target: NativeProcessIdentity?
    private let responsibility: NativeForegroundResponsibility
    private let localResponsibility: Bool
    private var original: NativeProcessIdentity? { responsibility.original }
    private var initialActivity: [UInt32] { responsibility.initialActivity }
    private var touched = false
    private var finished = false
    private var restored = false
    private var activationUnsettled = false
    private let onPreparation: @MainActor (String) -> Void
    private let onCleanupUnknown: @MainActor () -> Void

    private init(
        target: BackgroundTarget,
        onPreparation: @escaping @MainActor (String) -> Void,
        onCleanupUnknown: @escaping @MainActor () -> Void
    ) {
        self.target = target.capturedProcessIdentity
        let owner = NativeForegroundResponsibility.current
        responsibility = owner ?? NativeForegroundResponsibility()
        localResponsibility = owner == nil
        self.onPreparation = onPreparation
        self.onCleanupUnknown = onCleanupUnknown
    }

    func record(_ effect: NativePreparationEffect) {
        if effect.phase == .willSend {
            touched = true
            responsibility.recordPreparation(target: target)
        }
    }

    private func state() -> State {
        responsibility.state(target: target)
    }

    @discardableResult func finish() async throws -> Bool {
        if finished {
            try requireSettledActivation()
            return restored
        }
        finished = true
        defer { stopObserving() }
        guard touched else {
            recordOutcome(.notNeeded)
            return false
        }
        // A foreground menu/pointer candidate can prepare another local
        // receiver inside this scope. It still needs foreground through input;
        // sharing the original owner must not turn that into early restitution.
        if responsibility.retainsForegroundForInput {
            recordOutcome(original == target ? .notNeeded : .retained)
            return false
        }
        guard let original, let target else {
            recordOutcome(.unavailable)
            return false
        }
        let outcome = await Self.restore(
            originalPID: original.pid, targetPID: target.pid, touched: touched,
            initialActivity: initialActivity, sample: state, check: responsibility.checkRestoration,
            activate: { [self] in
                guard let application = NSRunningApplication(processIdentifier: original.pid) else { return false }
                let background = BackgroundTarget(application)
                guard background.capturedProcessIdentity == original else { return false }
                let detail = "return original foreground before background input"
                let receipt = try await ForegroundActivation.request(
                    background,
                    check: {
                        try self.responsibility.checkRestoration()
                        let current = self.state()
                        guard current.originalCurrent, current.targetCurrent,
                            [original.pid, target.pid].contains(current.foregroundPID ?? 0),
                            !current.externalActivation, current.activity == self.initialActivity
                        else { throw DriverError("foreground preparation ownership ended", code: .inputUnavailable) }
                    }, onCleanupUnknown: onCleanupUnknown)
                // The activation adapter owns actual invoked/unknown evidence.
                // A scope-level willSend before its ownership checks would
                // leave a false pending click effect when the user takes over.
                if receipt.invoked { onPreparation(detail) }
                return receipt.invoked
            }, wait: { try await Task.sleep(for: .milliseconds(25)) },
            onUnsettled: { self.activationUnsettled = true })
        recordOutcome(outcome)
        restored = outcome == .restored
        try requireSettledActivation()
        return restored
    }

    private func requireSettledActivation() throws {
        try Self.requireSettledActivation(activationUnsettled)
    }

    static func requireSettledActivation(_ unsettled: Bool) throws {
        guard !unsettled else {
            throw DriverError(
                "foreground return may still be pending; requested input was not sent",
                code: .executionUnknown, effect: .possible,
                next: "observe the original target; do not replay an unconfirmed activation")
        }
    }

    private func recordOutcome(_ outcome: Outcome) {
        responsibility.recordRestoration(outcome)
        NativeInputActivity.current?.recordForegroundRestoration(
            outcome.rawValue, originalPID: original?.pid, targetPID: target?.pid)
    }

    private func stopObserving() {
        if localResponsibility { responsibility.stop() }
    }

    static func ownsForeground(_ state: State, targetPID: pid_t, initialActivity: [UInt32]) -> Bool {
        state.originalCurrent && state.targetCurrent && state.foregroundPID == targetPID
            && !state.externalActivation && state.activity == initialActivity
    }

    /// The same bounded restoration is exercised with deterministic native
    /// ports. A missing/changed owner skips this optional effect, not the input.
    static func restore(
        originalPID: pid_t, targetPID: pid_t, touched: Bool, initialActivity: [UInt32],
        sample: () -> State, check: () throws -> Void, activate: () async throws -> Bool,
        wait: () async throws -> Void, onUnsettled: () -> Void = {}
    ) async -> Outcome {
        guard touched, originalPID != targetPID else { return .notNeeded }
        let initial = sample()
        NativeInputActivity.current?.recordForegroundObservation(initial.foregroundPID)
        guard initial.originalCurrent, initial.targetCurrent else { return .unavailable }
        guard !initial.externalActivation, initial.activity == initialActivity else { return .ownershipLost }
        if initial.foregroundPID == originalPID { return .notNeeded }
        guard initial.foregroundPID != nil else { return .unknown }
        guard ownsForeground(initial, targetPID: targetPID, initialActivity: initialActivity) else { return .ownershipLost }
        var activationMayBePending = false
        func finish(_ outcome: Outcome, current: State) -> Outcome {
            if activationMayBePending && !(current.originalCurrent && current.foregroundPID == originalPID) {
                onUnsettled()
            }
            return outcome
        }
        do {
            try check()
            guard ownsForeground(sample(), targetPID: targetPID, initialActivity: initialActivity) else {
                return .ownershipLost
            }
            // From entry until a definite not-invoked result, the asynchronous
            // helper may have queued activation even if its receipt is lost.
            activationMayBePending = true
            do {
                guard try await activate() else { return .failed }
            } catch let failure as DriverError where failure.effect == nil
                && [.inputUnavailable, .staleTarget].contains(failure.code)
            {
                activationMayBePending = false
                return finish(.unknown, current: sample())
            } catch {
                // A lost helper receipt does not prove failure or withdrawal.
                // Give its queued request the same bounded observation as a
                // returned invocation. Cancellation/deadline is rechecked below.
            }
            // Shared check enforces the original request deadline. This local
            // bound gives an accepted asynchronous request time to settle, but
            // cannot extend the public call or start a later recovery watcher.
            for attempt in 0..<40 {
                try check()
                let current = sample()
                NativeInputActivity.current?.recordForegroundObservation(current.foregroundPID)
                guard current.originalCurrent, current.targetCurrent else { return finish(.unavailable, current: current) }
                guard !current.externalActivation, current.activity == initialActivity else {
                    return finish(.ownershipLost, current: current)
                }
                if current.foregroundPID == originalPID { return .restored }
                guard current.foregroundPID == targetPID else { return finish(.ownershipLost, current: current) }
                if attempt < 39 { try await wait() }
            }
            return finish(.unknown, current: sample())
        } catch {
            // A check after dispatch can fail because the original target was
            // closed or the task cancelled. Neither withdraws queued activation.
            let current = sample()
            if current.externalActivation || current.activity != initialActivity {
                return finish(.ownershipLost, current: current)
            }
            return finish(.unknown, current: current)
        }
    }

    static func perform<T>(
        target: BackgroundTarget,
        onPreparation: @escaping @MainActor (String) -> Void,
        onCleanupUnknown: @escaping @MainActor () -> Void = {}, body: () async throws -> T
    ) async throws -> T {
        // A completed restitution cannot own a later local-activity request.
        // Give that request its own scope so any actual activation is still
        // returned and any unsettled return continues to block requested input.
        if let current, !current.finished, current.target == target.capturedProcessIdentity {
            return try await body()
        }
        let scope = NativeBackgroundPreparationScope(
            target: target, onPreparation: onPreparation,
            onCleanupUnknown: onCleanupUnknown)
        return try await $current.withValue(scope) {
            do {
                let result = try await body()
                try await scope.finish()
                return result
            } catch {
                // Keep a pending restoration's effect classification even if
                // the original preparation also failed. No requested input has
                // happened inside this scope.
                try await scope.finish()
                throw error
            }
        }
    }
}
