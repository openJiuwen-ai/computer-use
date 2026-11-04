// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices

/// One operation owns its original foreground across every selected candidate.
/// This journal does not restore after input or qualify unrelated AX routes.
@MainActor final class NativeForegroundResponsibility {
    @TaskLocal static var current: NativeForegroundResponsibility?

    struct Snapshot {
        var foregroundPID: pid_t?
        var activity: [UInt32]
    }

    private(set) var original: NativeProcessIdentity?
    private(set) var initialActivity = [UInt32]()
    private let snapshot: @MainActor () -> Snapshot
    private let process: @MainActor (pid_t) -> NativeProcessIdentity?
    private let check: @MainActor () throws -> Void
    private let watchActivation: Bool
    private var activationObserver: NSObjectProtocol?
    private var targets = Set<NativeProcessIdentity>()
    private var target: NativeProcessIdentity?
    private var touched = false
    private var foregroundObligation = false
    private var inputStarted = false
    private var ownershipLost = false
    private var restored = false
    private(set) var retainsForegroundForInput = false

    init(
        snapshot: @escaping @MainActor () -> Snapshot = NativeForegroundResponsibility.desktopSnapshot,
        process: @escaping @MainActor (pid_t) -> NativeProcessIdentity? = NativeProcessIdentity.read,
        check: @escaping @MainActor () throws -> Void = RequestDiagnostics.checkCancellation,
        watchActivation: Bool = true
    ) {
        self.snapshot = snapshot
        self.process = process
        self.check = check
        self.watchActivation = watchActivation
    }

    private func claim() {
        stop()
        targets.removeAll()
        let initial = snapshot()
        original = initial.foregroundPID.flatMap(process)
        initialActivity = initial.activity
        if watchActivation {
            activationObserver = NSWorkspace.shared.notificationCenter.addObserver(
                forName: NSWorkspace.didActivateApplicationNotification, object: nil, queue: .main
            ) { [weak self] notification in
                let app = notification.userInfo?[NSWorkspace.applicationUserInfoKey] as? NSRunningApplication
                MainActor.assumeIsolated {
                    guard let self, let app, let identity = self.process(app.processIdentifier) else { return }
                    self.recordActivation(identity)
                }
            }
        }
    }

    static func desktopSnapshot() -> Snapshot {
        .init(foregroundPID: NSWorkspace.shared.frontmostApplication?.processIdentifier, activity:
            [CGEventType.leftMouseDown, .rightMouseDown, .otherMouseDown, .keyDown, .flagsChanged, .scrollWheel].map {
                CGEventSource.counterForEventType(.hidSystemState, eventType: $0)
            })
    }

    func recordPreparation(target: NativeProcessIdentity?, foreground: Bool = false) {
        // The user may keep working between admission and our first effect.
        // Only the live context at this first claim can become our original.
        if !touched || foreground && !foregroundObligation { claim() }
        touched = true
        self.target = target
        if let target { targets.insert(target) }
        if foreground {
            foregroundObligation = true
            restored = false
        }
    }

    /// A candidate's dependency ends with that candidate; its outstanding
    /// foreground responsibility remains owned by the whole operation.
    func beginCandidate() { retainsForegroundForInput = false }
    func requireForegroundForCandidate() { retainsForegroundForInput = true }

    func recordActivation(_ identity: NativeProcessIdentity) {
        guard foregroundObligation, identity != original, !targets.contains(identity) else { return }
        ownershipLost = true
    }

    /// Borrow an already acquired native-call observation. This only records
    /// an actual excursion; it performs no query, restoration or input gate.
    func observeForeground(_ pid: pid_t?) {
        if touched, let pid, let original, pid != original.pid, targets.contains(where: { $0.pid == pid }) {
            foregroundObligation = true
            restored = false
        }
    }

    /// Shared ownership checks constrain restoration/repeated foreground
    /// acquisition only. They never become a common input or AX admission gate.
    func permitsForegroundPreparation() -> Bool {
        guard foregroundObligation else { return true }
        _ = state(target: target)
        return !ownershipLost
    }

    func state(target: NativeProcessIdentity?) -> NativeBackgroundPreparationScope.State {
        let current = snapshot()
        observeForeground(current.foregroundPID)
        // A requested global event may itself change HID counters. The
        // before-input owner can use them; no post-input restoration is claimed.
        if foregroundObligation, !inputStarted, current.activity != initialActivity { ownershipLost = true }
        if foregroundObligation, let pid = current.foregroundPID, pid != original?.pid,
            !targets.contains(where: { $0.pid == pid }) { ownershipLost = true }
        return .init(
            foregroundPID: current.foregroundPID,
            originalCurrent: original.map { process($0.pid) == $0 } ?? false,
            targetCurrent: target.map { process($0.pid) == $0 } ?? false,
            activity: current.activity, externalActivation: ownershipLost)
    }

    /// The requested-input barrier remains purely local, with no desktop or
    /// provider query and no change to dispatch/confirmation authority.
    func recordRequestedInput() { inputStarted = true }

    func recordRestoration(_ outcome: NativeBackgroundPreparationScope.Outcome) {
        if outcome == .restored {
            restored = true
            foregroundObligation = true
        }
        if outcome == .ownershipLost, foregroundObligation { ownershipLost = true }
    }

    /// Restoration concerns process ownership and the original operation
    /// deadline. An ended target Element/Window is not its receiving object.
    func checkRestoration() throws { try check() }

    func report(to activity: NativeInputActivity) {
        guard touched else { return }
        let current = state(target: target)
        let outcome: String
        if current.externalActivation { outcome = "ownershipLost" }
        else if !current.originalCurrent { outcome = "unknown" }
        else if current.foregroundPID == original?.pid { outcome = restored ? "restored" : "notNeeded" }
        else if current.foregroundPID == target?.pid, current.targetCurrent { outcome = "retained" }
        else { outcome = "unknown" }
        activity.recordForegroundRestoration(outcome, originalPID: original?.pid, targetPID: target?.pid)
    }

    func stop() {
        if let activationObserver { NSWorkspace.shared.notificationCenter.removeObserver(activationObserver) }
        activationObserver = nil
    }
}
