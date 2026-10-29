// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices

/// Owns process/window preparation receipts, independently of keyboard receivers.
/// Receipts describe notifications sent; actual receiver checks stay in input units.
@MainActor final class SyntheticFocusCoordinator {
    static let shared = SyntheticFocusCoordinator()

    struct Receipt: Equatable {
        let process: NativeProcessIdentity
        let window: CGWindowID
        let keyWindowPreparation: Bool
    }
    private var prepared: [pid_t: Receipt] = [:]
    private var observers: [pid_t: AXObserver] = [:]
    private var activationObserver: NSObjectProtocol?

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
            ) { [weak self] _ in MainActor.assumeIsolated { self?.prepared.removeAll() } }
        }
        guard observers[pid] == nil else { return }
        var observer: AXObserver?
        guard
            AXObserverCreate(
                pid,
                { _, element, _, context in
                    guard let context else { return }
                    MainActor.assumeIsolated {
                        var pid: pid_t = 0
                        if AXUIElementGetPid(element, &pid) == .success {
                            Unmanaged<SyntheticFocusCoordinator>.fromOpaque(context).takeUnretainedValue().prepared
                                .removeValue(forKey: pid)
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
        observers[pid] = observer
    }

    func stop() {
        for observer in observers.values {
            CFRunLoopRemoveSource(CFRunLoopGetMain(), AXObserverGetRunLoopSource(observer), .commonModes)
        }
        observers.removeAll()
        prepared.removeAll()
        if let activationObserver { NSWorkspace.shared.notificationCenter.removeObserver(activationObserver) }
        activationObserver = nil
    }
}
