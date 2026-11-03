// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices

/// Owns only foreground changes caused by a background preparation. It ends
/// before requested input: restoring after a click can dismiss its new popup.
/// No requested input, retry selection or foreground-dependent route lives here.
@MainActor final class NativeBackgroundPreparationScope {
    @TaskLocal static var current: NativeBackgroundPreparationScope?

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
    private let initialActivity: [UInt32]
    private var activationObserver: NSObjectProtocol?
    private var externalActivation = false
    private var touched = false
    private var finished = false
    private var restored = false
    private let check: @MainActor () throws -> Void
    private let onPreparation: @MainActor (String) -> Void
    private let onCleanupUnknown: @MainActor () -> Void

    private init(
        target: BackgroundTarget, check: @escaping @MainActor () throws -> Void,
        onPreparation: @escaping @MainActor (String) -> Void,
        onCleanupUnknown: @escaping @MainActor () -> Void
    ) {
        self.target = target.capturedProcessIdentity
        original = NSWorkspace.shared.frontmostApplication.flatMap { NativeProcessIdentity.read($0.processIdentifier) }
        initialActivity = Self.activity()
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
    private static func activity() -> [UInt32] {
        [CGEventType.leftMouseDown, .rightMouseDown, .otherMouseDown, .keyDown, .flagsChanged, .scrollWheel].map {
            CGEventSource.counterForEventType(.hidSystemState, eventType: $0)
        }
    }

    func record(_ effect: NativePreparationEffect) {
        if effect.phase == .willSend { touched = true }
    }

    private func state() -> State {
        .init(
            foregroundPID: NSWorkspace.shared.frontmostApplication?.processIdentifier,
            originalCurrent: original.map { NativeProcessIdentity.read($0.pid) == $0 } ?? false,
            targetCurrent: target.map { NativeProcessIdentity.read($0.pid) == $0 } ?? false,
            activity: Self.activity(), externalActivation: externalActivation)
    }

    @discardableResult func finish() async -> Bool {
        guard !finished else { return restored }
        finished = true
        defer { stopObserving() }
        guard let original, let target else {
            recordOutcome(.unavailable)
            return false
        }
        let outcome = await Self.restore(
            originalPID: original.pid, targetPID: target.pid, touched: touched,
            initialActivity: initialActivity, sample: state, check: check,
            activate: { [self] in
                guard let application = NSRunningApplication(processIdentifier: original.pid) else { return false }
                let background = BackgroundTarget(application)
                guard background.capturedProcessIdentity == original else { return false }
                let detail = "return original foreground before background input"
                let receipt = try await ForegroundActivation.request(
                    background,
                    check: {
                        try self.check()
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
            }, wait: { try await Task.sleep(for: .milliseconds(10)) })
        recordOutcome(outcome)
        restored = outcome == .restored
        return restored
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
        wait: () async throws -> Void
    ) async -> Outcome {
        guard touched, originalPID != targetPID else { return .notNeeded }
        let initial = sample()
        NativeInputActivity.current?.recordForegroundObservation(initial.foregroundPID)
        guard initial.originalCurrent, initial.targetCurrent else { return .unavailable }
        guard !initial.externalActivation, initial.activity == initialActivity else { return .ownershipLost }
        if initial.foregroundPID == originalPID { return .notNeeded }
        guard initial.foregroundPID != nil else { return .unknown }
        guard ownsForeground(initial, targetPID: targetPID, initialActivity: initialActivity) else { return .ownershipLost }
        do {
            try check()
            guard ownsForeground(sample(), targetPID: targetPID, initialActivity: initialActivity) else {
                return .ownershipLost
            }
            guard try await activate() else { return .failed }
            for attempt in 0..<12 {
                try check()
                let current = sample()
                NativeInputActivity.current?.recordForegroundObservation(current.foregroundPID)
                guard current.originalCurrent, current.targetCurrent else { return .unavailable }
                guard !current.externalActivation, current.activity == initialActivity else { return .ownershipLost }
                if current.foregroundPID == originalPID { return .restored }
                guard current.foregroundPID == targetPID else { return .ownershipLost }
                if attempt < 11 { try await wait() }
            }
            return .unknown
        } catch {
            let current = sample()
            if current.externalActivation || current.activity != initialActivity { return .ownershipLost }
            return .unknown
        }
    }

    static func perform<T>(
        target: BackgroundTarget, check: @escaping @MainActor () throws -> Void,
        onPreparation: @escaping @MainActor (String) -> Void,
        onCleanupUnknown: @escaping @MainActor () -> Void = {}, body: () async throws -> T
    ) async throws -> T {
        if let current, current.target == target.capturedProcessIdentity { return try await body() }
        let scope = NativeBackgroundPreparationScope(
            target: target, check: check, onPreparation: onPreparation,
            onCleanupUnknown: onCleanupUnknown)
        return try await $current.withValue(scope) {
            do {
                let result = try await body()
                await scope.finish()
                return result
            } catch {
                await scope.finish()
                throw error
            }
        }
    }
}
