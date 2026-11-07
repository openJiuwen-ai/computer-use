// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices

/// Change hints belong to one process incarnation. Notifications never query
/// provider content, mutate directory lifetimes, or authorize another input.
@MainActor final class NativeAppObserver {
    struct Stamp: Sendable, Equatable {
        let session: String
        let application: String
        let incarnation: String
        let token: String
        var wire: [String: Any] {
            ["session": session, "application": application, "incarnation": incarnation, "token": token]
        }
    }
    struct Status {
        let stamp: Stamp?
        let notifications: [String]
        var available: Bool { stamp != nil }
    }
    struct Change: Sendable {
        let stamp: Stamp?
        let reason: String
        let events: [String]
        let elapsedMs: Double
    }
    struct Registration {
        let observer: AXObserver?
        let notifications: [String]
        let stop: @MainActor () -> Void
    }
    struct Services {
        var process: @MainActor (pid_t) -> NativeProcessIdentity? = { NativeProcessIdentity.read($0) }
        var register: @MainActor (pid_t, UnsafeMutableRawPointer) throws -> Registration? = {
            try NativeAppObserver.register($0, context: $1)
        }
    }
    private final class Record {
        let owner: NSRunningApplication
        let process: NativeProcessIdentity
        let application: String
        let session: String
        let incarnation = UUID().uuidString
        let registration: Registration
        var epoch: UInt64 = 0
        var last = [String: UInt64]()
        init(owner: NSRunningApplication, process: NativeProcessIdentity, application: String, session: String,
             registration: Registration) {
            self.owner = owner; self.process = process; self.application = application
            self.session = session; self.registration = registration
        }
        func stamp() -> Stamp {
            Stamp(session: session, application: application, incarnation: incarnation, token: "\(incarnation):\(epoch)")
        }
        func events(after: UInt64) -> [String] {
            last.filter { $0.value > after }.sorted { $0.value < $1.value }.map(\.key)
        }
    }
    private struct Waiter {
        let pid: pid_t
        let incarnation: String
        let session: String
        let after: UInt64
        let started: TimeInterval
        let continuation: CheckedContinuation<Change, Error>
        var timer: Task<Void, Never>?
    }
    private let services: Services
    private var records = [pid_t: Record]()
    private var waiters = [UUID: Waiter]()
    private var termination: NSObjectProtocol?
    private var closed = false

    init(services: Services = Services(), watchTermination: Bool = true) {
        self.services = services
        if watchTermination {
            termination = NSWorkspace.shared.notificationCenter.addObserver(
                forName: NSWorkspace.didTerminateApplicationNotification, object: nil, queue: .main
            ) { [weak self] notification in
                let owner = notification.userInfo?[NSWorkspace.applicationUserInfoKey] as? NSRunningApplication
                MainActor.assumeIsolated {
                    guard let owner,
                        let record = self?.records[owner.processIdentifier], record.owner.isEqual(owner),
                        record.owner.isTerminated else { return }
                    self?.retire(owner.processIdentifier)
                }
            }
        }
    }

    private static func register(_ pid: pid_t, context: UnsafeMutableRawPointer) throws -> Registration? {
        guard AXIsProcessTrusted() else { return nil }
        let metric = RequestDiagnostics.metrics?.start(.appObserverRegistration)
        defer { metric?.end() }
        var observer: AXObserver?
        let status = AXObserverCreate(pid, { observer, _, name, context in
            guard let context else { return }
            MainActor.assumeIsolated {
                Unmanaged<NativeAppObserver>.fromOpaque(context).takeUnretainedValue()
                    .notified(observer, name: name as String)
            }
        }, &observer)
        guard status == .success, let observer else { return nil }
        let root = AXUIElementCreateApplication(pid)
        AXUIElementSetMessagingTimeout(root, 0.05)
        let names = [
            "AXWindowCreated", "AXUIElementDestroyed", "AXLayoutChanged", "AXFocusedUIElementChanged",
            "AXMainWindowChanged", "AXFocusedWindowChanged", "AXValueChanged", "AXSelectedTextChanged",
            "AXMenuOpened", "AXMenuClosed"
        ]
        var supported = [String](), statuses = [String: Int32]()
        do {
            for name in names {
                try RequestDiagnostics.checkCancellation()
                let result = AXObserverAddNotification(observer, root, name as CFString, context)
                statuses[name] = result.rawValue
                if result == .success { supported.append(name) }
            }
        } catch {
            for name in supported { _ = AXObserverRemoveNotification(observer, root, name as CFString) }
            throw error
        }
        // A provider may become available after AX opt-in/initialization. Only
        // a definite unsupported declaration can survive as an unavailable
        // lifetime record; transient registration failures may be tried later.
        if supported.isEmpty && statuses.values.contains(where: { $0 != AXError.notificationUnsupported.rawValue }) {
            return nil
        }
        CFRunLoopAddSource(CFRunLoopGetMain(), AXObserverGetRunLoopSource(observer), .commonModes)
        diagnose(["kind": "app_observer_registration", "pid": pid, "statuses": statuses])
        return Registration(observer: observer, notifications: supported, stop: {
            CFRunLoopRemoveSource(CFRunLoopGetMain(), AXObserverGetRunLoopSource(observer), .commonModes)
            for name in supported { _ = AXObserverRemoveNotification(observer, root, name as CFString) }
        })
    }

    func status(owner: NSRunningApplication, application: String, session: String,
                expected: NativeProcessIdentity?, arm: Bool = true) throws -> Status {
        guard !closed else { return Status(stamp: nil, notifications: []) }
        let pid = owner.processIdentifier
        let current = services.process(pid)
        if let prior = records[pid], let current, current != prior.process { retire(pid) }
        // Unknown kernel facts suspend event assistance without destroying the
        // retained subscription. Only a known different incarnation retires it.
        guard let process = current, process == expected else { return Status(stamp: nil, notifications: []) }
        if let prior = records[pid], prior.process != process || prior.application != application || prior.session != session {
            retire(pid)
        }
        if records[pid] == nil {
            guard arm else { return Status(stamp: nil, notifications: []) }
            guard let registration = try services.register(pid, Unmanaged.passUnretained(self).toOpaque()) else {
                return Status(stamp: nil, notifications: [])
            }
            records[pid] = Record(owner: owner, process: process, application: application, session: session,
                registration: registration)
        }
        let record = records[pid]!
        return Status(stamp: record.registration.notifications.isEmpty ? nil : record.stamp(),
                      notifications: record.registration.notifications)
    }

    /// Requested-input admission borrows only the already-armed record. This
    /// performs no registration, provider call or kernel/foreground query.
    func latest(_ armed: Stamp) -> Stamp? {
        guard !closed, let record = records.values.first(where: {
            $0.incarnation == armed.incarnation && $0.application == armed.application && $0.session == armed.session
        }), !record.registration.notifications.isEmpty else { return nil }
        let prefix = record.incarnation + ":"
        guard armed.token.hasPrefix(prefix), let epoch = UInt64(armed.token.dropFirst(prefix.count)),
            epoch <= record.epoch else { return nil }
        return record.stamp()
    }

    /// Borrow an already-armed change marker without querying or registering the provider.
    func windowScopeEpoch(owner: NSRunningApplication, session: String) -> (incarnation: String, epoch: UInt64)? {
        guard !closed, let record = records[owner.processIdentifier], record.owner.isEqual(owner),
            record.session == session else { return nil }
        let notifications = ["AXWindowCreated", "AXUIElementDestroyed", "AXMenuOpened", "AXMenuClosed"]
        let epoch = notifications.compactMap { record.last[$0] }.max() ?? 0
        return (record.incarnation, epoch)
    }

    private func notified(_ observer: AXObserver, name: String) {
        guard let record = records.values.first(where: {
            $0.registration.observer.map { CFEqual($0, observer) } == true
        }) else { return }
        recordEvent(pid: record.process.pid, incarnation: record.incarnation, name: name)
    }

    /// The same leaf event adapter is used by AX callbacks and mechanism probes.
    func recordEvent(pid: pid_t, incarnation: String, name: String) {
        guard let record = records[pid], record.incarnation == incarnation,
            record.registration.notifications.contains(name) else { return }
        record.epoch += 1
        record.last[name] = record.epoch
        for id in waiters.keys.filter({ waiters[$0]?.incarnation == incarnation }) { complete(id, reason: "event") }
    }

    func wait(owner: NSRunningApplication, application: String, session: String, after token: String,
              timeoutMs: Int) async throws -> Change {
        guard let record = records[owner.processIdentifier], record.application == application,
            !record.registration.notifications.isEmpty else {
            return Change(stamp: nil, reason: "unavailable", events: [], elapsedMs: 0)
        }
        guard record.session == session else {
            throw DriverError("App change token belongs to another session", code: .staleTarget)
        }
        let prefix = record.incarnation + ":"
        guard token.hasPrefix(prefix), let after = UInt64(token.dropFirst(prefix.count)), after <= record.epoch else {
            throw DriverError("App change token belongs to another process incarnation", code: .staleTarget)
        }
        if record.epoch > after {
            return Change(stamp: record.stamp(), reason: "event", events: record.events(after: after), elapsedMs: 0)
        }
        let metric = RequestDiagnostics.metrics?.start(.appObserverWait)
        defer { metric?.end() }
        let started = ProcessInfo.processInfo.systemUptime
        let duration = min(Double(timeoutMs) / 1000, (RequestDiagnostics.deadline ?? .infinity) - started)
        guard duration > 0 else { throw RequestDeadlineExceeded() }
        let id = UUID()
        return try await withTaskCancellationHandler {
            try await withCheckedThrowingContinuation { continuation in
                guard !Task.isCancelled else { continuation.resume(throwing: CancellationError()); return }
                waiters[id] = Waiter(pid: record.process.pid, incarnation: record.incarnation, session: session,
                    after: after, started: started, continuation: continuation)
                waiters[id]?.timer = Task { @MainActor [weak self] in
                    do { try await Task.sleep(for: .seconds(duration)) } catch { return }
                    self?.complete(id, reason: "timeout")
                }
            }
        } onCancel: {
            Task { @MainActor [weak self] in self?.cancel(id) }
        }
    }

    private func complete(_ id: UUID, reason: String) {
        guard let waiter = waiters.removeValue(forKey: id) else { return }
        waiter.timer?.cancel()
        let record = records[waiter.pid].flatMap {
            $0.incarnation == waiter.incarnation && $0.session == waiter.session ? $0 : nil
        }
        waiter.continuation.resume(returning: Change(
            stamp: record?.stamp(), reason: record == nil ? "unavailable" : reason,
            events: reason == "event" ? record?.events(after: waiter.after) ?? [] : [],
            elapsedMs: max(0, (ProcessInfo.processInfo.systemUptime - waiter.started) * 1000)))
    }
    private func cancel(_ id: UUID) {
        guard let waiter = waiters.removeValue(forKey: id) else { return }
        waiter.timer?.cancel()
        waiter.continuation.resume(throwing: CancellationError())
    }
    private func retire(_ pid: pid_t) {
        records.removeValue(forKey: pid)?.registration.stop()
        for id in waiters.keys.filter({ waiters[$0]?.pid == pid }) { complete(id, reason: "unavailable") }
    }
    func stop() {
        guard !closed else { return }
        closed = true
        if let termination { NSWorkspace.shared.notificationCenter.removeObserver(termination) }
        termination = nil
        for id in Array(waiters.keys) { cancel(id) }
        for pid in Array(records.keys) { retire(pid) }
    }
}
