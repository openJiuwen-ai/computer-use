// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices

/// One admitted execute step owns its audit. No input, AX queries, waits or
/// admission decisions originate here. A returned post is submission evidence,
/// never proof of application consumption or an exclusive lock on human input.
@MainActor final class NativeInputActivity {
    @TaskLocal static var current: NativeInputActivity?
    @TaskLocal static var stage = Stage.input
    private static let keyboardEventDetailsEnabled =
        ProcessInfo.processInfo.environment["COMPUTER_USE_DIAGNOSE_KEY_EVENTS"] == "1"

    enum Stage: String, Hashable { case preparation, input, cleanup }
    enum Device: String, Hashable { case mouse, keyboard, focus, element, clipboard, application }
    enum Transport: String, Hashable { case globalHID, process, accessibility, activation, windowServer, clipboard, launch }

    private struct ImpactEffect: Hashable {
        let stage: Stage
        let device: Device
        let transport: Transport
        let mechanism: String
        var state: String
        var wire: [String: Any] {
            ["stage": stage.rawValue, "device": device.rawValue, "transport": transport.rawValue,
             "mechanism": mechanism, "state": state]
        }
        var sortKey: String { [stage.rawValue, device.rawValue, transport.rawValue, mechanism, state].joined(separator: "\t") }
    }
    private struct PendingEffect {
        let resources: [String]
        let effect: ImpactEffect
    }

    @MainActor struct Effect {
        fileprivate let id: Int
        fileprivate let fields: [String: Any]
        fileprivate let before: [String: Any]
        fileprivate let started: TimeInterval
        fileprivate let owner: NativeInputActivity

        func returned(nativeInvoked: Bool? = true, status: Int? = nil, accepted: Bool? = nil) {
            owner.returned(self, nativeInvoked: nativeInvoked, status: status, accepted: accepted)
        }
    }

    private let metadata: [String: Any]
    private let clock: () -> TimeInterval
    private let snapshot: @MainActor () -> [String: Any]
    private let emit: ([String: Any]) -> Void
    private let started: TimeInterval
    private let initial: [String: Any]
    private let wallClock: ISO8601DateFormatter = {
        let value = ISO8601DateFormatter()
        value.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        value.timeZone = TimeZone(secondsFromGMT: 0)
        return value
    }()
    private var sequence = 0
    private var effects = 0
    private var pending = [Int: PendingEffect]()
    private var completedEffects = Set<ImpactEffect>()
    private var shared = Set<String>()
    private var possible = Set<String>()
    private var foregroundReceiver = false
    private var ended = false
    private var finalImpact: [String: Any]?
    private var previousForeground: pid_t?
    private var foregroundChangeObserved = false
    private var foregroundObservationUnknown = false
    private var clipboardRestoration = "notUsed"
    private var foregroundRestorations = Set<String>()
    private var summaryCompacted = false

    init(
        operation: String, step: Int64, command: String, intent: String, target: [String: Any], globalInputAllowed: Bool,
        clock: @escaping () -> TimeInterval = { ProcessInfo.processInfo.systemUptime },
        snapshot: @escaping @MainActor () -> [String: Any] = NativeInputActivity.desktopSnapshot,
        emit: @escaping ([String: Any]) -> Void = { diagnose($0) }
    ) {
        self.clock = clock
        self.snapshot = snapshot
        self.emit = emit
        started = clock()
        initial = snapshot()
        previousForeground = initial["foregroundPID"] as? Int32
        foregroundObservationUnknown = previousForeground == nil
        metadata = [
            "kind": "input_activity", "version": 1, "activityId": UUID().uuidString, "operation": operation,
            "step": step, "command": command, "intent": intent, "target": target, "globalInputAllowed": globalInputAllowed
        ]
        record("begin", ["desktop": initial])
    }

    static func desktopSnapshot() -> [String: Any] {
        let position = CGEvent(source: nil)?.location
        return [
            "foregroundPID": NSWorkspace.shared.frontmostApplication?.processIdentifier as Any? ?? NSNull(),
            "foregroundSource": "NSWorkspace.frontmostApplication",
            "cursor": position.map { ["x": $0.x, "y": $0.y] } as Any? ?? NSNull()
        ]
    }

    /// Called only at an actual effect boundary, not when generating offers.
    static func begin(
        device: Device, transport: Transport, mechanism: String, pid: pid_t? = nil, window: CGWindowID? = nil,
        event: CGEvent? = nil, stage: Stage? = nil, presenterPID: pid_t? = nil
    ) -> Effect? {
        let release = event.map { [.leftMouseUp, .rightMouseUp, .otherMouseUp, .keyUp].contains($0.type) } ?? false
        return current?.begin(
            device: device, transport: transport, mechanism: mechanism, pid: pid, window: window,
            eventType: event.map { Int($0.type.rawValue) }, point: device == .mouse ? event?.location : nil,
            stage: stage ?? (release ? .cleanup : Self.stage), presenterPID: presenterPID, event: event)
    }

    /// The injected ports exercise the same recorder without sending OS input.
    func begin(
        device: Device, transport: Transport, mechanism: String, pid: pid_t? = nil, window: CGWindowID? = nil,
        eventType: Int? = nil, point: CGPoint? = nil, stage: Stage = .input, presenterPID: pid_t? = nil,
        event: CGEvent? = nil
    ) -> Effect? {
        guard !ended else { return nil }
        effects += 1
        let before = snapshot()
        observeForeground(before)
        let resources = Self.resources(device: device, transport: transport, pid: pid, desktop: before)
        let scalars = mechanism.unicodeScalars
        let summaryMechanism = String(String.UnicodeScalarView(scalars.prefix(512)))
        if scalars.count > 512 { summaryCompacted = true }
        pending[effects] = PendingEffect(
            resources: resources,
            effect: ImpactEffect(stage: stage, device: device, transport: transport, mechanism: summaryMechanism, state: "possible"))
        if device == .clipboard { clipboardRestoration = "unknown" }
        let presenter = presenterPID ?? pid
        let receiver = device == .keyboard && presenter != nil && (before["foregroundPID"] as? Int32) == presenter
        foregroundReceiver = foregroundReceiver || receiver
        var fields: [String: Any] = [
            "effectId": effects, "stage": stage.rawValue, "device": device.rawValue, "transport": transport.rawValue,
            "mechanism": mechanism, "targetPID": pid as Any? ?? NSNull(),
            "recipientPID": (transport == .process ? pid : nil) as Any? ?? NSNull(),
            "window": window as Any? ?? NSNull(), "presenterPID": presenter as Any? ?? NSNull(),
            "eventType": eventType as Any? ?? NSNull(),
            "eventRole": eventType.map { type in
                [CGEventType.leftMouseUp, .rightMouseUp, .otherMouseUp, .keyUp].contains { Int($0.rawValue) == type }
                    ? "release" : "event"
            } as Any? ?? NSNull(), "point": point.map { ["x": $0.x, "y": $0.y] } as Any? ?? NSNull(),
            "sharedResources": resources, "foregroundReceiver": receiver
        ]
        // Read only the already-constructed event. Text payload and the
        // operation's structured receipt stay outside this opt-in trace.
        if Self.keyboardEventDetailsEnabled, device == .keyboard, let event,
            [.keyDown, .keyUp, .flagsChanged].contains(event.type)
        {
            fields["keycode"] = event.getIntegerValueField(.keyboardEventKeycode)
            fields["flags"] = event.flags.rawValue
        }
        let effect = Effect(id: effects, fields: fields, before: before, started: clock(), owner: self)
        record("willCall", fields.merging(["before": before]) { _, new in new })
        return effect
    }

    func keyboardDestination(pid: pid_t, window: CGWindowID?, focusedWindow: CGWindowID?) {
        guard !ended else { return }
        record(
            "keyboardDestination",
            [
                "expectedPID": pid, "expectedWindow": window as Any? ?? NSNull(),
                "expectedFocusedWindow": focusedWindow as Any? ?? NSNull()
            ])
    }

    private static func resources(device: Device, transport: Transport, pid: pid_t?, desktop: [String: Any]) -> [String]
    {
        if transport == .globalHID { return device == .mouse ? ["mouse"] : device == .keyboard ? ["keyboard"] : [] }
        if device == .clipboard { return ["clipboard"] }
        if transport == .activation || transport == .windowServer { return ["focus"] }
        if device == .focus {
            // Background AX focus can stay local. Missing foreground evidence
            // stays a possible shared-focus request, not a no-impact assertion.
            guard let foreground = desktop["foregroundPID"] as? Int32, let pid else { return ["focus"] }
            return foreground == pid ? ["focus"] : []
        }
        return []
    }

    private func returned(_ effect: Effect, nativeInvoked: Bool?, status: Int?, accepted: Bool?) {
        guard !ended, let pendingEffect = pending.removeValue(forKey: effect.id) else { return }
        var summary = pendingEffect.effect
        summary.state = nativeInvoked == true ? "invoked" : nativeInvoked == false ? "notInvoked" : "possible"
        completedEffects.insert(summary)
        let after = snapshot()
        observeForeground(after)
        let resources = effect.fields["sharedResources"] as? [String] ?? []
        if nativeInvoked == true { shared.formUnion(resources) }
        if nativeInvoked == nil { possible.formUnion(resources) }
        let changed = Self.foregroundNetChanged(effect.before, after)
        record(
            "didReturn",
            effect.fields.merging([
                "nativeInvoked": nativeInvoked as Any? ?? NSNull(), "nativeStatus": status as Any? ?? NSNull(),
                "nativeAccepted": accepted as Any? ?? NSNull(), "after": after, "foregroundNetChanged": changed,
                "durationMs": max(0, clock() - effect.started) * 1000
            ]) { _, new in new })
    }

    @discardableResult func finish(dispatch: String, cleanup: String) -> [String: Any] {
        if let finalImpact { return finalImpact }
        ended = true
        let final = snapshot()
        observeForeground(final)
        possible.formUnion(pending.values.flatMap { $0.resources })
        completedEffects.formUnion(pending.values.map { $0.effect })
        var summaries = completedEffects
        if summaries.count > 128 {
            summaryCompacted = true
            // Preserve resource/transport/state evidence when mechanism names
            // are numerous. Repeated keyboard characters never expand this set.
            summaries = Set(summaries.map {
                ImpactEffect(stage: $0.stage, device: $0.device, transport: $0.transport,
                             mechanism: "multiple recorded native calls", state: $0.state)
            })
        }
        let ordered = summaries.sorted {
            // Keep every possible global-input category before lower-impact
            // detail if a future adapter introduces additional combinations.
            if ($0.transport == .globalHID) != ($1.transport == .globalHID) { return $0.transport == .globalHID }
            return $0.sortKey < $1.sortKey
        }
        // Completeness covers SDK mutation invocations during this execute
        // range, including its synchronous/awaited preparation and cleanup.
        // It does not claim provider consumption, business effects, or include
        // later session-lifetime cleanup and unrelated user/app activity.
        let impact: [String: Any] = [
            "coverage": summaryCompacted || ordered.count > 128 ? "partial" : "complete",
            "effects": ordered.prefix(128).map { $0.wire },
            "omittedEffects": max(0, ordered.count - 128),
            "foregroundChanged": foregroundChangeObserved ? "yes" : foregroundObservationUnknown ? "unknown" : "no",
            "foregroundRestoration": foregroundRestoration,
            "clipboardRestoration": clipboardRestoration
        ]
        finalImpact = impact
        record(
            "end",
            [
                "dispatch": dispatch, "cleanup": cleanup, "effectCount": effects,
                "pendingEffects": pending.keys.sorted(), "sharedResources": shared.sorted(),
                "possibleSharedResources": possible.sorted(), "foregroundReceiver": foregroundReceiver,
                "desktop": final, "foregroundNetChanged": Self.foregroundNetChanged(initial, final),
                "foregroundChanged": impact["foregroundChanged"] as Any,
                "durationMs": max(0, clock() - started) * 1000
            ])
        return impact
    }

    private func observeForeground(_ snapshot: [String: Any]) {
        let current = snapshot["foregroundPID"] as? Int32
        NativeForegroundResponsibility.current?.observeForeground(current)
        if let previousForeground, let current {
            foregroundChangeObserved = foregroundChangeObserved || previousForeground != current
        } else { foregroundObservationUnknown = true }
        previousForeground = current
    }

    func recordClipboardRestoration(_ result: PasteboardTransaction.RestoreResult) {
        guard !ended, clipboardRestoration != "notUsed" else { return }
        switch result {
        case .restored: clipboardRestoration = "restored"
        case .ownershipLost: clipboardRestoration = "ownershipLost"
        case .failed: clipboardRestoration = "failed"
        }
    }

    /// Observations already obtained by the preparation owner, including a
    /// delayed activation between native-call snapshots. Recording never reads
    /// the desktop, restores focus, or changes input/cleanup progress.
    func recordForegroundObservation(_ pid: pid_t?) {
        guard !ended else { return }
        observeForeground(["foregroundPID": pid as Any? ?? NSNull()])
    }

    func recordForegroundRestoration(_ outcome: String, originalPID: pid_t?, targetPID: pid_t?) {
        guard !ended else { return }
        let known = ["notNeeded", "restored", "ownershipLost", "retained", "unavailable", "failed", "unknown"]
        let result = known.contains(outcome) ? outcome : "unknown"
        foregroundRestorations.insert(result)
        // A confirmed restoration reports an observed excursion, even if its
        // asynchronous activation fell between the effect-boundary snapshots.
        if result == "restored", let originalPID, let targetPID, originalPID != targetPID {
            foregroundChangeObserved = true
        }
        record("foregroundRestoration", [
            "outcome": result, "originalPID": originalPID as Any? ?? NSNull(),
            "targetPID": targetPID as Any? ?? NSNull()
        ])
    }

    /// Retain unresolved impact when several preparations share an execution
    /// step. This summary is evidence only, never retry or cleanup policy.
    private var foregroundRestoration: String {
        ["failed", "unknown", "retained", "unavailable", "ownershipLost", "restored", "notNeeded"]
            .first { foregroundRestorations.contains($0) } ?? "notUsed"
    }

    private static func foregroundNetChanged(_ before: [String: Any], _ after: [String: Any]) -> String {
        guard let first = before["foregroundPID"] as? Int32, let last = after["foregroundPID"] as? Int32 else {
            return "unknown"
        }
        return first != last ? "yes" : "no"
    }

    private func record(_ phase: String, _ fields: [String: Any]) {
        sequence += 1
        emit(
            metadata.merging(fields) { _, new in new }.merging([
                "phase": phase, "sequence": sequence, "uptime": clock(),
                "nativeTimestamp": wallClock.string(from: Date())
            ]) { _, new in new })
    }

    /// Configure failures are outside this boundary; no mutation was invoked.
    static func accessibility(_ element: AXUIElement, mechanism: String, focus: Bool, body: () -> AXError) -> AXError {
        guard current != nil else { return body() }
        var pid: pid_t = 0
        let owner = AXUIElementGetPid(element, &pid) == .success ? pid : nil
        let effect = begin(
            device: focus ? .focus : .element, transport: .accessibility, mechanism: mechanism, pid: owner)
        let status = body()
        effect?.returned(status: Int(status.rawValue))
        return status
    }
}
