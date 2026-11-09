// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices

/// One operation owns its original foreground across every selected candidate.
/// Restoration phases borrow this one owner; it never qualifies AX input.
@MainActor final class NativeForegroundResponsibility {
    @TaskLocal static var current: NativeForegroundResponsibility?

    struct Snapshot {
        var foregroundPID: pid_t?
        var activity: [UInt32]
    }
    enum ActivityState { case settled, ownInputPending, userActivity }

    private(set) var original: NativeProcessIdentity?
    private(set) var originalWindow: NativeForegroundWindow?
    private(set) var initialActivity = [UInt32]()
    private var ownInput = [UInt32]()
    var expectedActivity: [UInt32] { zip(initialActivity, ownInput).map { $0 &+ $1 } }
    var hasForegroundObligation: Bool { touched && foregroundObligation }
    private let snapshot: @MainActor () -> Snapshot
    private let process: @MainActor (pid_t) -> NativeProcessIdentity?
    private var check: @MainActor () throws -> Void
    private let window: @MainActor (pid_t) -> NativeForegroundWindow?
    private let watchActivation: Bool
    private var activationObserver: NSObjectProtocol?
    private var targets = Set<NativeProcessIdentity>()
    private var target: NativeProcessIdentity?
    private var touched = false
    private var foregroundObligation = false
    private var inputStarted = false
    private var ownershipLost = false
    private var restored = false
    private var lastRestoration: NativeForegroundRestorationScope.Outcome?
    private var candidateIdentity: String?
    private(set) var retainsForegroundForInput = false
    private(set) var requiredKeyWindow: AXUIElement?
    private(set) var retainedPresentation: (@MainActor () throws -> NativeForegroundResult.Presentation)?
    private var expectedWindow: NativeForegroundWindow?

    init(
        snapshot: @escaping @MainActor () -> Snapshot = NativeForegroundResponsibility.desktopSnapshot,
        process: @escaping @MainActor (pid_t) -> NativeProcessIdentity? = NativeProcessIdentity.read,
        window: @escaping @MainActor (pid_t) -> NativeForegroundWindow? = NativeForegroundWindow.focused,
        check: @escaping @MainActor () throws -> Void = RequestDiagnostics.checkCancellation,
        watchActivation: Bool = true
    ) {
        self.snapshot = snapshot
        self.process = process
        self.window = window
        self.check = check
        self.watchActivation = watchActivation
    }

    private func claim() {
        stop()
        targets.removeAll()
        let initial = snapshot()
        original = initial.foregroundPID.flatMap(process)
        originalWindow = initial.foregroundPID.flatMap(window)
        initialActivity = initial.activity
        ownInput = Array(repeating: 0, count: initial.activity.count)
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
    static let activityTypes: [CGEventType] = [.leftMouseDown, .rightMouseDown, .otherMouseDown, .keyDown, .flagsChanged, .scrollWheel]

    func activityState(_ values: [UInt32]) -> ActivityState {
        guard values.count == initialActivity.count, ownInput.count == initialActivity.count else { return .ownInputPending }
        var pending = false
        for index in values.indices {
            let delta = values[index] &- initialActivity[index]
            if delta > ownInput[index] { return .userActivity }
            if delta < ownInput[index] { pending = true }
        }
        return pending ? .ownInputPending : .settled
    }

    /// Count only our actual unconditional global post. A queued own event
    /// not reflected in HID counters is pending, never invented user activity.
    func willPostGlobalInput(_ type: CGEventType) {
        guard touched else { return }
        let current = snapshot()
        observeForeground(current.foregroundPID)
        if foregroundObligation, activityState(current.activity) == .userActivity { ownershipLost = true }
        if let index = Self.activityTypes.firstIndex(of: type), ownInput.indices.contains(index) { ownInput[index] &+= 1 }
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
    func beginCandidate(_ identity: String? = nil) {
        // Receiver preparation and requested dispatch are separate RPCs for
        // the same primitive. Its declared dependency survives that boundary.
        // An unaddressed step or a different candidate starts a new dependency.
        if identity == nil || identity != candidateIdentity {
            retainsForegroundForInput = false
            requiredKeyWindow = nil
            candidateIdentity = identity
        }
    }
    func requireForegroundForCandidate(keyWindow: AXUIElement? = nil) {
        retainsForegroundForInput = true
        if let keyWindow { requiredKeyWindow = keyWindow }
    }
    /// Only addressed input with a fixed recipient may discharge
    /// its preparation-only foreground dependency before requested input.
    func releaseForegroundForAddressedInput() {
        retainsForegroundForInput = false
        requiredKeyWindow = nil
    }

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

    func state(target: NativeProcessIdentity?) -> NativeForegroundRestorationScope.State {
        let current = snapshot()
        observeForeground(current.foregroundPID)
        let originalWindowCurrent = originalWindow.map { originalWindow in
            guard current.foregroundPID == original?.pid else { return false }
            return originalWindow.matches(current.foregroundPID.flatMap(window))
        } ?? true
        if touched, current.foregroundPID == original?.pid, !originalWindowCurrent,
            targets.contains(where: { $0.pid == current.foregroundPID }) {
            foregroundObligation = true
            restored = false
        }
        if foregroundObligation, activityState(current.activity) == .userActivity { ownershipLost = true }
        if foregroundObligation, let pid = current.foregroundPID, pid != original?.pid,
            !targets.contains(where: { $0.pid == pid }) { ownershipLost = true }
        return .init(
            foregroundPID: current.foregroundPID,
            originalCurrent: original.map { process($0.pid) == $0 } ?? false,
            targetCurrent: target.map { process($0.pid) == $0 } ?? false,
            activity: current.activity, externalActivation: ownershipLost,
            originalWindowCurrent: originalWindowCurrent)
    }

    /// The requested-input barrier remains purely local, with no desktop or
    /// provider query and no change to dispatch/confirmation authority.
    func recordRequestedInput() { inputStarted = true }

    func recordRestoration(_ outcome: NativeForegroundRestorationScope.Outcome) {
        lastRestoration = outcome
        if outcome == .restored {
            restored = true
            foregroundObligation = true
            retainedPresentation = nil
        }
        if outcome == .ownershipLost, foregroundObligation { ownershipLost = true }
    }

    /// Restoration concerns process ownership and the original operation
    /// deadline. An ended target Element/Window is not its receiving object.
    func checkRestoration() throws { try check() }

    func retainPresentation(_ probe: @escaping @MainActor () throws -> NativeForegroundResult.Presentation) {
        retainedPresentation = probe
    }

    /// Suspend only a proven presented interaction. No work is scheduled
    /// between public calls; the passive observer only relinquishes ownership.
    func suspend() -> Bool {
        guard retainedPresentation != nil, hasForegroundObligation, !restored, !ownershipLost else { return false }
        expectedWindow = snapshot().foregroundPID.flatMap(window)
        return true
    }

    func resume(check: @escaping @MainActor () throws -> Void) -> Bool {
        self.check = check
        let current = state(target: target)
        if let expectedWindow, !expectedWindow.matches(current.foregroundPID.flatMap(window)) { ownershipLost = true }
        guard !ownershipLost else { return false }
        inputStarted = false
        beginCandidate()
        return true
    }

    func report(to activity: NativeInputActivity) {
        guard touched else { return }
        let current = state(target: target)
        let outcome: String
        if current.externalActivation { outcome = "ownershipLost" }
        else if !current.originalCurrent { outcome = "unknown" }
        else if current.foregroundPID == original?.pid && current.originalWindowCurrent {
            outcome = restored ? "restored" : "notNeeded"
        }
        else if inputStarted && activityState(current.activity) == .ownInputPending { outcome = "unknown" }
        else if let value = lastRestoration, [.failed, .unknown, .unavailable].contains(value) { outcome = value.rawValue }
        else if current.foregroundPID == target?.pid, current.targetCurrent { outcome = "retained" }
        else { outcome = "unknown" }
        activity.recordForegroundRestoration(outcome, originalPID: original?.pid, targetPID: target?.pid)
    }

    func stop() {
        if let activationObserver { NSWorkspace.shared.notificationCenter.removeObserver(activationObserver) }
        activationObserver = nil
    }
}
