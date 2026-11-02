// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices

/// Owns process/window preparation receipts and posted local activation leases,
/// independently of keyboard receivers. Neither proves application consumption.
@MainActor final class SyntheticFocusCoordinator {
    static let shared = SyntheticFocusCoordinator()

    struct Receipt: Equatable {
        let process: NativeProcessIdentity
        let window: CGWindowID
        let keyWindowPreparation: Bool
    }
    private var prepared: [pid_t: Receipt] = [:]
    private struct ProcessObserver {
        let observer: AXObserver
        let process: NativeProcessIdentity
    }
    private var observers: [pid_t: ProcessObserver] = [:]
    private var activationObserver: NSObjectProtocol?
    private var localActivations = NativeLocalActivationLeases()

    func localActivationIsCurrent(_ target: BackgroundTarget) -> Bool {
        let pid = target.application.processIdentifier
        guard let process = target.capturedProcessIdentity, process.pid == pid else { return false }
        let current = NativeProcessIdentity.read(pid)
        let foreground = NSWorkspace.shared.frontmostApplication?.processIdentifier
        if foreground == pid || current.map({ $0 != process }) == true {
            if let retained = localActivations.take(pid, matching: process) {
                diagnoseLease(retained, trigger: "currentQuery", outcome: "dropped",
                              reason: foreground == pid ? "foreground" : "processChanged")
            }
            return false
        }
        return localActivations.isCurrent(process, current: current, foregroundPID: foreground)
    }

    /// Called only after the unit actually posts subtype 1. Keep the identity
    /// validated before that post; a later lookup cannot adopt a replacement.
    func recordLocalActivationPosted(_ target: BackgroundTarget, process: NativeProcessIdentity) {
        guard target.application.processIdentifier == process.pid, target.capturedProcessIdentity == process else {
            diagnoseLease(process, trigger: "recordPosted", outcome: "dropped", reason: "targetIdentityMismatch")
            return
        }
        observe(process.pid)
        let current = NativeProcessIdentity.read(process.pid)
        let foreground = NSWorkspace.shared.frontmostApplication?.processIdentifier
        if foreground == process.pid || current.map({ $0 != process }) == true {
            _ = localActivations.take(process.pid, matching: process)
            diagnoseLease(process, trigger: "recordPosted", outcome: "dropped",
                          reason: foreground == process.pid ? "foreground" : "processChanged")
            return
        }
        if let previous = localActivations.recordPosted(process), previous != process {
            diagnoseLease(previous, trigger: "recordPosted", outcome: "dropped", reason: "processChanged")
        }
        diagnoseLease(process, trigger: "recordPosted", outcome: "retained", reason: "activationPosted",
                      effect: "unconfirmed")
    }

    func isCurrent(_ target: BackgroundTarget, window: WindowGeometry.WindowMapping, needsKeyWindow: Bool = false)
        -> Bool
    {
        guard window.pid == target.application.processIdentifier, let process = NativeProcessIdentity.read(window.pid)
        else { return false }
        guard let receipt = prepared[window.pid], receipt.process == process, receipt.window == window.id else {
            return false
        }
        return !needsKeyWindow || receipt.keyWindowPreparation
    }

    func remember(_ target: BackgroundTarget, window: WindowGeometry.WindowMapping, needsKeyWindow: Bool) throws {
        guard let process = NativeProcessIdentity.read(window.pid) else {
            throw DriverError("focus recipient disappeared", code: .staleTarget)
        }
        let prior = isCurrent(target, window: window, needsKeyWindow: true)
        prepared[window.pid] = Receipt(
            process: process, window: window.id, keyWindowPreparation: needsKeyWindow || prior)
    }

    func observe(_ pid: pid_t) {
        if activationObserver == nil {
            activationObserver = NSWorkspace.shared.notificationCenter.addObserver(
                forName: NSWorkspace.didActivateApplicationNotification, object: nil, queue: .main
            ) { [weak self] notification in
                let activated = notification.userInfo?[NSWorkspace.applicationUserInfoKey] as? NSRunningApplication
                MainActor.assumeIsolated {
                    self?.workspaceActivationChanged(activated)
                }
            }
        }
        // Without an incarnation, an AX callback cannot identify which posted
        // lease it may release. Workspace/session cleanup still owns that lease.
        guard let process = NativeProcessIdentity.read(pid) else { return }
        if let prior = observers[pid] {
            // A known new incarnation needs its own AX observer, never an old
            // PID cache. A failed query above does not establish replacement.
            guard prior.process != process else { return }
            CFRunLoopRemoveSource(CFRunLoopGetMain(), AXObserverGetRunLoopSource(prior.observer), .commonModes)
            observers.removeValue(forKey: pid)
            prepared.removeValue(forKey: pid)
            if let previous = localActivations.take(pid) {
                diagnoseLease(previous, trigger: "observe", outcome: "dropped", reason: "processChanged")
            }
        }
        var observer: AXObserver?
        guard
            AXObserverCreate(
                pid,
                { observer, element, _, context in
                    guard let context else { return }
                    MainActor.assumeIsolated {
                        var pid: pid_t = 0
                        if AXUIElementGetPid(element, &pid) == .success {
                            Unmanaged<SyntheticFocusCoordinator>.fromOpaque(context).takeUnretainedValue()
                                .applicationDeactivated(pid, observer: observer)
                        }
                    }
                }, &observer) == .success, let observer
        else { return }
        let root = AXUIElementCreateApplication(pid)
        let context = Unmanaged.passUnretained(self).toOpaque()
        // Content/editor focus changes do not invalidate window transport or blur
        // a newly created editor. Deactivation invalidates only this process.
        guard
            AXObserverAddNotification(observer, root, kAXApplicationDeactivatedNotification as CFString, context)
                == .success
        else { return }
        CFRunLoopAddSource(CFRunLoopGetMain(), AXObserverGetRunLoopSource(observer), .commonModes)
        observers[pid] = ProcessObserver(observer: observer, process: process)
    }

    private func applicationDeactivated(_ pid: pid_t, observer: AXObserver) {
        guard let registered = observers[pid], CFEqual(registered.observer, observer) else { return }
        prepared.removeValue(forKey: pid)
        // The provider already reported deactivation. End ownership without
        // sending another notification or affecting a newly foreground app.
        if let process = localActivations.take(pid, matching: registered.process) {
            diagnoseLease(process, trigger: "applicationDeactivated", outcome: "dropped", reason: "providerDeactivated")
        }
    }

    private func workspaceActivationChanged(_ activated: NSRunningApplication?) {
        prepared.removeAll()
        // A queued activation notification still establishes that the user gave
        // this application real activation, even after a later switch elsewhere.
        // Match its registration lifetime before reading the current kernel
        // identity, so an old PID notification cannot claim a replacement lease.
        if let activated, !activated.isTerminated,
            let registered = NSRunningApplication(processIdentifier: activated.processIdentifier),
            registered.isEqual(activated), registered.launchDate == activated.launchDate,
            let process = localActivations.takeForWorkspaceActivation(
                NativeProcessIdentity.read(activated.processIdentifier))
        {
            diagnoseLease(process, trigger: "workspaceActivationChanged", outcome: "dropped", reason: "workspaceActivated")
        }
        cleanupLocalActivations(trigger: "workspaceActivationChanged")
    }

    private func cleanupLocalActivations(trigger: String) {
        for process in localActivations.takeAll() {
            let decision = NativeLocalActivationLeases.cleanup(
                process, current: NativeProcessIdentity.read(process.pid),
                foregroundPID: NSWorkspace.shared.frontmostApplication?.processIdentifier)
            guard decision == .deactivate else {
                diagnoseLease(process, trigger: trigger,
                              outcome: decision == .processUnavailable || decision == .foregroundUnavailable
                                ? "unknown" : "dropped", reason: decision.rawValue)
                continue
            }
            guard let event = SyntheticFocusEvents.applicationActivation(active: false) else {
                diagnoseLease(process, trigger: trigger, outcome: "unknown", reason: "eventUnavailable",
                              effect: "unconfirmed")
                continue
            }
            let activity = NativeInputActivity.begin(
                device: .focus, transport: .process, mechanism: "processApplicationDeactivationCleanup",
                pid: process.pid, event: event, stage: .preparation)
            event.postToPid(process.pid)
            activity?.returned()
            // This diagnostic survives session.stop without a TaskLocal audit.
            // postToPid returning confirms submission only, never consumption.
            diagnoseLease(process, trigger: trigger, outcome: "sent", reason: "leaseEnded", effect: "unconfirmed")
        }
    }

    private func diagnoseLease(
        _ process: NativeProcessIdentity, trigger: String, outcome: String, reason: String, effect: String = "none"
    ) {
        let recording = trigger == "recordPosted"
        diagnose([
            "kind": "synthetic_local_activation_lease", "phase": recording ? "recordPosted" : "cleanup",
            "trigger": trigger,
            "targetPID": process.pid, "processIncarnation": process.incarnation, "outcome": outcome,
            "reason": reason, "effect": effect, "confirmation": "unavailable",
            "transport": "process",
            "mechanism": recording ? "processApplicationActivation" : "processApplicationDeactivationCleanup"
        ] as [String: Any])
    }

    func stop() {
        for record in observers.values {
            CFRunLoopRemoveSource(CFRunLoopGetMain(), AXObserverGetRunLoopSource(record.observer), .commonModes)
        }
        observers.removeAll()
        prepared.removeAll()
        if let activationObserver { NSWorkspace.shared.notificationCenter.removeObserver(activationObserver) }
        activationObserver = nil
        cleanupLocalActivations(trigger: "sessionStop")
    }
}
