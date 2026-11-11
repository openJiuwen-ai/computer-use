// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices

/// Local before/after-input phases borrow the operation's one foreground owner.
/// Optional restitution never changes requested-input admission or replay.
@MainActor final class NativeForegroundRestorationScope {
    @TaskLocal static var current: NativeForegroundRestorationScope?

    enum Outcome: String { case notNeeded, restored, ownershipLost, retained, unavailable, failed, unknown }
    enum InputState { case notDispatched, completed, uncertain }
    struct State {
        var foregroundPID: pid_t?
        var originalCurrent: Bool
        var targetCurrent: Bool
        var activity: [UInt32]
        var externalActivation: Bool
        var originalWindowCurrent: Bool = true
    }

    let target: NativeProcessIdentity?
    private let responsibility: NativeForegroundResponsibility
    private let localResponsibility: Bool
    private var original: NativeProcessIdentity? { responsibility.original }
    private var initialActivity: [UInt32] { responsibility.expectedActivity }
    private var touched = false
    private var finished = false
    private var restored = false
    private var activationUnsettled = false
    private let afterInput: Bool
    private let inputState: @MainActor () -> InputState
    private let presentation: @MainActor () throws -> NativeForegroundResult.Presentation
    private var beforeInputWindows: Set<CGWindowID>?
    private let onPreparation: @MainActor (String) -> Void
    private let onCleanupUnknown: @MainActor () -> Void

    private init(
        target: BackgroundTarget,
        onPreparation: @escaping @MainActor (String) -> Void,
        onCleanupUnknown: @escaping @MainActor () -> Void,
        afterInput: Bool, inputState: @escaping @MainActor () -> InputState,
        presentation: @escaping @MainActor () throws -> NativeForegroundResult.Presentation
    ) {
        self.target = target.capturedProcessIdentity
        self.afterInput = afterInput
        self.inputState = inputState
        self.presentation = presentation
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

    func willActivate() {
        guard !finished else { return }
        touched = true
        responsibility.recordPreparation(target: target, foreground: true)
    }

    func willDispatchInput() {
        guard afterInput, !finished, touched || responsibility.hasForegroundObligation, let target else { return }
        // AXMain/AXFocused can change the concrete foreground window without
        // an application activation. Sample after preparation, before choosing
        // result inspection; the pre-preparation obligation may still be false.
        _ = responsibility.state(target: target)
        guard responsibility.hasForegroundObligation else { return }
        beforeInputWindows = Self.visibleWindows(target.pid)
    }

    private static func visibleWindows(_ pid: pid_t) -> Set<CGWindowID>? {
        guard let rows = CGWindowListCopyWindowInfo(.optionOnScreenOnly, kCGNullWindowID) as? [[String: Any]] else { return nil }
        return Set(rows.compactMap {
            ($0[kCGWindowOwnerPID as String] as? NSNumber)?.int32Value == pid
                ? ($0[kCGWindowNumber as String] as? NSNumber)?.uint32Value : nil
        })
    }

    private func resultDecision(_ pid: pid_t) -> NativeForegroundResult.Decision {
        do {
            try responsibility.checkRestoration()
            if let retained = responsibility.retainedPresentation {
                switch try retained() {
                case .present: return .transientResult
                case .unknown: return .unknownResult
                case .absent: break
                }
            }
            switch try presentation() {
            case .present:
                responsibility.retainPresentation(presentation)
                return .transientResult
            case .unknown: return .unknownResult
            case .absent: break
            }
            guard let beforeInputWindows, let after = Self.visibleWindows(pid) else { return .unknownResult }
            let added = after.subtracting(beforeInputWindows)
            guard !added.isEmpty else { return .noTransientResult }
            let root = AXUIElementCreateApplication(pid)
            AXUIElementSetMessagingTimeout(root, 0.1)
            guard let windows = AXAccess.read(root, kAXWindowsAttribute).value as? [AXUIElement] else { return .unknownResult }
            var facts = [NativeForegroundResult.Window]()
            for window in windows {
                try responsibility.checkRestoration()
                guard let id = BackgroundEvents.nativeWindowID(window), added.contains(id) else { continue }
                AXUIElementSetMessagingTimeout(window, 0.05)
                let role = AXAccess.read(window, kAXRoleAttribute).value as? String
                let subrole = AXAccess.read(window, kAXSubroleAttribute).value as? String
                let modal = [kAXDialogSubrole, kAXSystemDialogSubrole].contains(subrole ?? "")
                    ? AXAccess.read(window, kAXModalAttribute).value as? Bool : nil
                facts.append(.init(id: id, role: role, subrole: subrole, modal: modal))
            }
            return NativeForegroundResult.decide(before: beforeInputWindows, after: after, windows: facts, knownTransient: false)
        } catch { return .unknownResult }
    }

    /// The actual phase composition is exercised without desktop input. Only
    /// presented content stays intact; completion need not create a new window.
    static func finishPhase(
        input: InputState, activity: NativeForegroundResponsibility.ActivityState,
        inspect: () -> NativeForegroundResult.Decision,
        restore: () async throws -> Bool, retain: (String) -> Void
    ) async rethrows -> Bool {
        if input == .uncertain { retain("inputProgressUnconfirmed"); return false }
        if activity == .userActivity { retain("userTakeover"); return false }
        if activity == .ownInputPending { retain("ownHIDCounterPending"); return false }
        if input == .completed {
            // A native menu action can return while its closing animation is
            // still presented. Only an interaction retained before this input
            // has that settling obligation. Opening a fresh menu returns at
            // once and keeps the requested menu; this never sends more input.
            let continuation = NativeForegroundResponsibility.current.flatMap {
                $0.retainedPresentation != nil ? $0 : nil
            }
            var decision = inspect()
            if decision == .transientResult, let continuation {
                do {
                    let settled = try await NativeMenuPresentationSettlement.wait(
                        initiallyPresented: true, deadline: RequestDiagnostics.deadline ?? .infinity,
                        check: continuation.checkRestoration,
                        presented: { decision = inspect(); return decision == .transientResult })
                    diagnose(["kind": "foreground_result_settle", "samples": settled.samples,
                              "durationMs": settled.elapsed * 1000, "result": decision.rawValue])
                } catch {
                    retain("presentationSettleUnconfirmed")
                    return false
                }
            }
            guard decision == .persistentResult || decision == .noTransientResult else {
                retain(decision.rawValue); return false
            }
        }
        return try await restore()
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
        guard touched || responsibility.hasForegroundObligation else {
            recordOutcome(.notNeeded)
            return false
        }
        // A foreground menu/pointer candidate can prepare another local
        // receiver inside this scope. It still needs foreground through input;
        // sharing the original owner must not turn that into early restitution.
        if !afterInput && (responsibility.retainsForegroundForInput || responsibility.retainedPresentation != nil) {
            recordOutcome(.retained)
            return false
        }
        guard let original, let target else {
            recordOutcome(.unavailable)
            return false
        }
        if afterInput {
            let current = state()
            if !current.originalCurrent || !current.targetCurrent {
                recordOutcome(.unavailable)
                return false
            }
            if current.externalActivation {
                recordOutcome(.ownershipLost)
                diagnose(["kind": "foreground_return", "reason": "userTakeover", "targetPID": target.pid])
                return false
            }
            if current.foregroundPID == original.pid && current.originalWindowCurrent {
                recordOutcome(.notNeeded)
                return false
            }
            return try await Self.finishPhase(
                input: inputState(), activity: responsibility.activityState(current.activity),
                inspect: { self.resultDecision(target.pid) }, restore: { try await self.restoreOwnedForeground(original, target) },
                retain: { reason in
                    self.recordOutcome(.retained)
                    diagnose(["kind": "foreground_return", "reason": reason, "targetPID": target.pid,
                              "originalPID": original.pid, "phase": "afterInput"])
                })
        }
        if responsibility.activityState(state().activity) == .ownInputPending {
            recordOutcome(.unknown)
            diagnose(["kind": "foreground_return", "reason": "ownHIDCounterPending",
                      "targetPID": target.pid, "originalPID": original.pid, "phase": "beforeInput"])
            return false
        }
        return try await restoreOwnedForeground(original, target)
    }

    private func restoreOwnedForeground(_ original: NativeProcessIdentity, _ target: NativeProcessIdentity) async throws -> Bool {
        let outcome = await Self.restore(
            originalPID: original.pid, targetPID: target.pid, touched: touched || responsibility.hasForegroundObligation,
            initialActivity: initialActivity, sample: state, check: responsibility.checkRestoration,
            activate: { [self] in
                guard let application = NSRunningApplication(processIdentifier: original.pid) else { return false }
                let background = BackgroundTarget(application)
                guard background.capturedProcessIdentity == original else { return false }
                let detail = afterInput ? "return original foreground after pointer input" : "return original foreground before background input"
                let check: @MainActor () throws -> Void = {
                        try self.responsibility.checkRestoration()
                        let current = self.state()
                        guard current.originalCurrent, current.targetCurrent,
                            [original.pid, target.pid].contains(current.foregroundPID ?? 0),
                            !current.externalActivation, current.activity == self.initialActivity
                        else { throw DriverError("foreground preparation ownership ended", code: .inputUnavailable) }
                    }
                let receipt = self.state().foregroundPID != original.pid ? try await ForegroundActivation.request(
                    background, check: check, onCleanupUnknown: onCleanupUnknown) : nil
                // The activation adapter owns actual invoked/unknown evidence.
                // A scope-level willSend before its ownership checks would
                // leave a false pending click effect when the user takes over.
                if receipt?.invoked == true { onPreparation(detail) }
                if receipt?.invoked == true, responsibility.originalWindow != nil {
                    for _ in 0..<24 {
                        try check()
                        if self.state().foregroundPID == original.pid && application.isActive { break }
                        try await Task.sleep(for: .milliseconds(25))
                    }
                    guard self.state().foregroundPID == original.pid else {
                        throw DriverError("original foreground activation is still pending", code: .executionUnknown,
                                          effect: .possible)
                    }
                }
                let windowInvoked = try responsibility.originalWindow?.restore(
                    application, check: check, onPreparation: onPreparation) ?? false
                return receipt?.invoked == true || windowInvoked
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
                "foreground return may still be pending",
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
        guard touched else { return .notNeeded }
        let initial = sample()
        NativeInputActivity.current?.recordForegroundObservation(initial.foregroundPID)
        guard initial.originalCurrent, initial.targetCurrent else { return .unavailable }
        guard !initial.externalActivation, initial.activity == initialActivity else { return .ownershipLost }
        if initial.foregroundPID == originalPID && initial.originalWindowCurrent { return .notNeeded }
        guard initial.foregroundPID != nil else { return .unknown }
        guard ownsForeground(initial, targetPID: targetPID, initialActivity: initialActivity) else { return .ownershipLost }
        var activationMayBePending = false
        func finish(_ outcome: Outcome, current: State) -> Outcome {
            if activationMayBePending && !(current.originalCurrent && current.foregroundPID == originalPID
                                          && current.originalWindowCurrent) {
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
                if current.foregroundPID == originalPID && current.originalWindowCurrent { return .restored }
                guard current.foregroundPID == targetPID || current.foregroundPID == originalPID else {
                    return finish(.ownershipLost, current: current)
                }
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
        onCleanupUnknown: @escaping @MainActor () -> Void = {},
        afterInput: Bool = false, inputState: @escaping @MainActor () -> InputState = { .notDispatched },
        presentation: @escaping @MainActor () throws -> NativeForegroundResult.Presentation = { .absent },
        body: @MainActor () async throws -> T
    ) async throws -> T {
        // A completed restitution cannot own a later local-activity request.
        // Give that request its own scope so any actual activation is still
        // returned and any unsettled return continues to block requested input.
        if let current, !current.finished, current.target == target.capturedProcessIdentity, current.afterInput == afterInput {
            return try await body()
        }
        let scope = NativeForegroundRestorationScope(
            target: target, onPreparation: onPreparation,
            onCleanupUnknown: onCleanupUnknown, afterInput: afterInput, inputState: inputState, presentation: presentation)
        @MainActor func run() async throws -> T { try await $current.withValue(scope) {
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
        } }
        if NativeForegroundResponsibility.current == nil {
            return try await NativeForegroundResponsibility.$current.withValue(scope.responsibility, operation: run)
        }
        return try await run()
    }
}
