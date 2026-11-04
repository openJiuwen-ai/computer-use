// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices

/// Owns foreground changes caused by preparation. Background preparation returns
/// the foreground before input; a foreground-dependent pointer returns it at
/// completion, unless doing so would dismiss a newly opened transient surface.
@MainActor final class NativeForegroundRestorationScope {
    @TaskLocal static var current: NativeForegroundRestorationScope?

    enum Outcome: String { case notNeeded, restored, ownershipLost, unavailable, failed, unknown }
    struct State {
        var foregroundPID: pid_t?
        var originalCurrent: Bool
        var targetCurrent: Bool
        var activity: [UInt32]
        var externalActivation: Bool
    }

    let target: NativeProcessIdentity?
    private let original: NativeProcessIdentity?
    private var expectedActivity: [UInt32]
    private var physicalOwnershipLost = false
    private var activationObserver: NSObjectProtocol?
    private var externalActivation = false
    private var touched = false
    private var finished = false
    private var restored = false
    private var activationUnsettled = false
    private let afterInput: Bool
    private let inputDispatched: @MainActor () -> Bool
    private var beforeInputWindows: Set<CGWindowID>?
    private let check: @MainActor () throws -> Void
    private let onPreparation: @MainActor (String) -> Void
    private let onCleanupUnknown: @MainActor () -> Void

    private init(
        target: BackgroundTarget, check: @escaping @MainActor () throws -> Void,
        onPreparation: @escaping @MainActor (String) -> Void,
        onCleanupUnknown: @escaping @MainActor () -> Void,
        afterInput: Bool = false, inputDispatched: @escaping @MainActor () -> Bool = { false }
    ) {
        self.afterInput = afterInput
        self.inputDispatched = inputDispatched
        self.target = target.capturedProcessIdentity
        original = NSWorkspace.shared.frontmostApplication.flatMap { NativeProcessIdentity.read($0.processIdentifier) }
        expectedActivity = Self.activity()
        self.check = check
        self.onPreparation = onPreparation
        self.onCleanupUnknown = onCleanupUnknown
        guard let original, let identity = self.target, original != identity else { return }
        activationObserver = NSWorkspace.shared.notificationCenter.addObserver(
            forName: NSWorkspace.didActivateApplicationNotification, object: nil, queue: .main
        ) { [weak self] notification in
            let app = notification.userInfo?[NSWorkspace.applicationUserInfoKey] as? NSRunningApplication
            MainActor.assumeIsolated {
                guard let self, let app, let process = NativeProcessIdentity.read(app.processIdentifier),
                    process != original, process != identity
                else { return }
                self.externalActivation = true
            }
        }
    }

    /// Physical activity is observed without an event tap, content recording or
    /// event suppression. Mouse movement alone does not transfer focus ownership.
    private static let activityTypes: [CGEventType] = [
        .leftMouseDown, .rightMouseDown, .otherMouseDown, .keyDown, .flagsChanged, .scrollWheel
    ]
    private static func activity() -> [UInt32] {
        activityTypes.map {
            CGEventSource.counterForEventType(.hidSystemState, eventType: $0)
        }
    }

    func record(_ effect: NativePreparationEffect) {
        if effect.phase == .willSend { touched = true }
    }

    func willActivate() { touched = true }

    func willDispatchInput() {
        guard afterInput, !finished, let target else { return }
        // Activation can itself reveal auxiliary windows. They are preparation
        // state, not a menu produced by the requested click.
        beforeInputWindows = Self.visibleWindows(target.pid)
    }

    /// HID counters include our posted events as well as hardware. Account for
    /// only this scope's actual posts; extra events still transfer ownership.
    func willPostGlobalInput(_ type: CGEventType) {
        guard afterInput, let index = Self.activityTypes.firstIndex(of: type), !finished else { return }
        if Self.activity() != expectedActivity { physicalOwnershipLost = true }
        expectedActivity[index] &+= 1
    }

    private static func visibleWindows(_ pid: pid_t) -> Set<CGWindowID>? {
        guard let rows = CGWindowListCopyWindowInfo(.optionOnScreenOnly, kCGNullWindowID) as? [[String: Any]] else {
            return nil
        }
        return Set(rows.compactMap { row in
            guard (row[kCGWindowOwnerPID as String] as? NSNumber)?.int32Value == pid else { return nil }
            return (row[kCGWindowNumber as String] as? NSNumber)?.uint32Value
        })
    }

    /// Returning focus must not undo an opened menu. Standard document/dialog
    /// windows survive deactivation; a new unclassified surface is preserved.
    /// This only governs optional restitution, never admission or replay.
    static func persistentWindow(role: String?, subrole: String?) -> Bool {
        role == kAXWindowRole && [kAXStandardWindowSubrole, kAXDialogSubrole, kAXSystemDialogSubrole]
            .contains(subrole ?? "")
    }

    private func preservesNewSurface(_ pid: pid_t) async -> Bool {
        guard afterInput, inputDispatched() else { return false }
        guard let beforeInputWindows else { return true }
        guard let application = NSRunningApplication(processIdentifier: pid) else { return true }
        // WindowServer can publish a document/dialog before its AX window is
        // enumerable. Settle only that classification, without another input
        // or a new operation budget; unresolved popups retain their effect.
        let settleUntil = ProcessInfo.processInfo.systemUptime + 0.5
        while true {
            do { try check() } catch { return true }
            guard let current = Self.visibleWindows(pid) else { return true }
            var newWindows = current.subtracting(beforeInputWindows)
            if newWindows.isEmpty { return false }
            let root = AXUIElementCreateApplication(pid)
            AXUIElementSetMessagingTimeout(root, 0.1)
            var sampled = [[String: Any]]()
            for window in AXAccess.read(root, kAXWindowsAttribute).value as? [AXUIElement] ?? [] {
                do { try check() } catch { return true }
                AXUIElementSetMessagingTimeout(window, 0.1)
                let id = BackgroundEvents.nativeWindowID(window)
                    ?? (try? NativeWindowUnit(application: application, window: window).mapping())?.id
                let role = AXAccess.read(window, kAXRoleAttribute).value as? String
                let subrole = AXAccess.read(window, kAXSubroleAttribute).value as? String
                sampled.append(["window": id as Any? ?? NSNull(), "role": role as Any? ?? NSNull(),
                                "subrole": subrole as Any? ?? NSNull()])
                if let id, Self.persistentWindow(role: role, subrole: subrole) { newWindows.remove(id) }
            }
            if newWindows.isEmpty { return false }
            diagnose(["kind": "foreground_surface_classification", "remaining": Array(newWindows),
                      "sampled": sampled, "targetPID": pid])
            if !Self.ownsForeground(state(), targetPID: pid, initialActivity: expectedActivity) { return false }
            guard ProcessInfo.processInfo.systemUptime < settleUntil else { return true }
            do { try await Task.sleep(for: .milliseconds(25)) } catch { return true }
        }
    }

    private func state() -> State {
        .init(
            foregroundPID: NSWorkspace.shared.frontmostApplication?.processIdentifier,
            originalCurrent: original.map { NativeProcessIdentity.read($0.pid) == $0 } ?? false,
            targetCurrent: target.map { NativeProcessIdentity.read($0.pid) == $0 } ?? false,
            activity: Self.activity(), externalActivation: externalActivation || physicalOwnershipLost)
    }

    @discardableResult func finish() async throws -> Bool {
        if finished {
            try requireSettledActivation()
            return restored
        }
        finished = true
        defer { stopObserving() }
        guard let original, let target else {
            recordOutcome(.unavailable)
            return false
        }
        if touched, original != target,
            Self.ownsForeground(state(), targetPID: target.pid, initialActivity: expectedActivity),
            await preservesNewSurface(target.pid)
        {
            recordOutcome(.unavailable)
            diagnose(["kind": "foreground_return", "reason": "new transient surface preserved",
                      "originalPID": original.pid, "targetPID": target.pid])
            return false
        }
        let outcome = await Self.restore(
            originalPID: original.pid, targetPID: target.pid, touched: touched,
            initialActivity: expectedActivity, sample: state, check: check,
            activate: { [self] in
                guard let application = NSRunningApplication(processIdentifier: original.pid) else { return false }
                let background = BackgroundTarget(application)
                guard background.capturedProcessIdentity == original else { return false }
                let detail = afterInput ? "return original foreground after pointer input"
                    : "return original foreground before background input"
                let receipt = try await ForegroundActivation.request(
                    background,
                    check: {
                        try self.check()
                        let current = self.state()
                        guard current.originalCurrent, current.targetCurrent,
                            [original.pid, target.pid].contains(current.foregroundPID ?? 0),
                            !current.externalActivation, current.activity == self.expectedActivity
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
                "foreground return may still be pending",
                code: .executionUnknown, effect: .possible,
                next: "observe the original target; do not replay an unconfirmed activation")
        }
    }

    private func recordOutcome(_ outcome: Outcome) {
        NativeInputActivity.current?.recordForegroundRestoration(
            outcome.rawValue, originalPID: original?.pid, targetPID: target?.pid)
    }

    private func stopObserving() {
        if let activationObserver { NSWorkspace.shared.notificationCenter.removeObserver(activationObserver) }
        activationObserver = nil
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
        target: BackgroundTarget, check: @escaping @MainActor () throws -> Void,
        onPreparation: @escaping @MainActor (String) -> Void,
        onCleanupUnknown: @escaping @MainActor () -> Void = {},
        afterInput: Bool = false, inputDispatched: @escaping @MainActor () -> Bool = { false },
        body: () async throws -> T
    ) async throws -> T {
        // A completed restitution cannot own a later local-activity request.
        // Give that request its own scope so any actual activation is still
        // returned and any unsettled return continues to block requested input.
        if let current, !current.finished, current.target == target.capturedProcessIdentity,
            current.afterInput == afterInput
        {
            return try await body()
        }
        let scope = NativeForegroundRestorationScope(
            target: target, check: check, onPreparation: onPreparation,
            onCleanupUnknown: onCleanupUnknown, afterInput: afterInput, inputDispatched: inputDispatched)
        return try await $current.withValue(scope) {
            do {
                let result = try await body()
                try await scope.finish()
                return result
            } catch {
                // Keep a pending restoration's effect classification even if
                // preparation or input also failed. The caller's receipt owns
                // any requested input already dispatched before this cleanup.
                try await scope.finish()
                throw error
            }
        }
    }
}
