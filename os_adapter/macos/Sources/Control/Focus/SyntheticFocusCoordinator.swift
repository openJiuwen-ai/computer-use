// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices

/// Owns process/window preparation receipts and posted local activation leases,
/// independently of keyboard receivers. Neither proves application consumption.
/// A posted activation is not a reversible resource: its opposite event can
/// dismiss the menu/editor produced by a completed request.
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
        // lease it may retire. Session teardown can still release its bookkeeping.
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
        // A change elsewhere does not revoke this process's internal receiver.
        // The native input unit still samples current key-window/receiver facts
        // before delivery. Only the newly active target's old preparation is
        // superseded here; provider deactivation owns its independent invalidation.
        // Workspace notifications can be queued across a completed background
        // preparation and foreground return. There is no occurrence timestamp,
        // so an old notification cannot invalidate freshly confirmed preparation
        // once its application is no longer actually foreground.
        guard let activated, !activated.isTerminated,
            let registered = NSRunningApplication(processIdentifier: activated.processIdentifier),
            registered.isEqual(activated), registered.launchDate == activated.launchDate,
            NSWorkspace.shared.frontmostApplication?.processIdentifier == activated.processIdentifier,
            let process = NativeProcessIdentity.read(activated.processIdentifier)
        else { return }
        if prepared[process.pid]?.process == process {
            prepared.removeValue(forKey: process.pid)
        }
        if let retired = localActivations.takeForWorkspaceActivation(process) {
            diagnoseLease(retired, trigger: "workspaceActivationChanged", outcome: "dropped", reason: "workspaceActivated")
        }
    }

    private func diagnoseLease(
        _ process: NativeProcessIdentity, trigger: String, outcome: String, reason: String, effect: String = "none"
    ) {
        let recording = trigger == "recordPosted"
        diagnose([
            "kind": "synthetic_local_activation_lease", "phase": recording ? "recordPosted" : "retired",
            "trigger": trigger,
            "targetPID": process.pid, "processIncarnation": process.incarnation, "outcome": outcome,
            "reason": reason, "effect": effect, "confirmation": "unavailable",
            "transport": "process",
            "mechanism": "processApplicationActivation"
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
        // The requested operation may have left a menu/editor open for the next
        // caller. Session teardown releases our observers and records, not that
        // application effect. There is no paired input to release for subtype 1.
        for process in localActivations.takeAll() {
            diagnoseLease(process, trigger: "sessionStop", outcome: "dropped", reason: "trackingEnded")
        }
    }
}
