// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices

/// One admitted execute step owns its audit. No input, AX queries, waits or
/// admission decisions originate here. A returned post is submission evidence,
/// never proof of application consumption or an exclusive lock on human input.
@MainActor final class NativeInputActivity {
    @TaskLocal static var current: NativeInputActivity?
    @TaskLocal static var stage = Stage.input

    enum Stage: String { case preparation, input }
    enum Device: String { case mouse, keyboard, focus, element }
    enum Transport: String { case globalHID, process, accessibility, activation, windowServer }

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
    private var pending = [Int: [String]]()
    private var shared = Set<String>()
    private var possible = Set<String>()
    private var foregroundReceiver = false
    private var ended = false

    init(
        operation: String, step: Int64, command: String, intent: String, target: [String: Any], mode: String,
        clock: @escaping () -> TimeInterval = { ProcessInfo.processInfo.systemUptime },
        snapshot: @escaping @MainActor () -> [String: Any] = NativeInputActivity.desktopSnapshot,
        emit: @escaping ([String: Any]) -> Void = { diagnose($0) }
    ) {
        self.clock = clock
        self.snapshot = snapshot
        self.emit = emit
        started = clock()
        initial = snapshot()
        metadata = [
            "kind": "input_activity", "version": 1, "activityId": UUID().uuidString, "operation": operation,
            "step": step, "command": command, "intent": intent, "target": target, "desktopMode": mode
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
        current?.begin(
            device: device, transport: transport, mechanism: mechanism, pid: pid, window: window,
            eventType: event.map { Int($0.type.rawValue) }, point: device == .mouse ? event?.location : nil,
            stage: stage ?? Self.stage, presenterPID: presenterPID)
    }

    /// The injected ports exercise the same recorder without sending OS input.
    func begin(
        device: Device, transport: Transport, mechanism: String, pid: pid_t? = nil, window: CGWindowID? = nil,
        eventType: Int? = nil, point: CGPoint? = nil, stage: Stage = .input, presenterPID: pid_t? = nil
    ) -> Effect? {
        guard !ended else { return nil }
        effects += 1
        let before = snapshot()
        let resources = Self.resources(device: device, transport: transport, pid: pid, desktop: before)
        pending[effects] = resources
        let presenter = presenterPID ?? pid
        let receiver = device == .keyboard && presenter != nil && (before["foregroundPID"] as? Int32) == presenter
        foregroundReceiver = foregroundReceiver || receiver
        let fields: [String: Any] = [
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
        guard !ended, pending.removeValue(forKey: effect.id) != nil else { return }
        let after = snapshot()
        let resources = effect.fields["sharedResources"] as? [String] ?? []
        if nativeInvoked == true { shared.formUnion(resources) }
        if nativeInvoked == nil { possible.formUnion(resources) }
        let changed = Self.foregroundChanged(effect.before, after)
        record(
            "didReturn",
            effect.fields.merging([
                "nativeInvoked": nativeInvoked as Any? ?? NSNull(), "nativeStatus": status as Any? ?? NSNull(),
                "nativeAccepted": accepted as Any? ?? NSNull(), "after": after, "foregroundChanged": changed,
                "durationMs": max(0, clock() - effect.started) * 1000
            ]) { _, new in new })
    }

    func finish(dispatch: String, cleanup: String) {
        guard !ended else { return }
        ended = true
        let final = snapshot()
        possible.formUnion(pending.values.flatMap { $0 })
        record(
            "end",
            [
                "dispatch": dispatch, "cleanup": cleanup, "effectCount": effects,
                "pendingEffects": pending.keys.sorted(), "sharedResources": shared.sorted(),
                "possibleSharedResources": possible.sorted(), "foregroundReceiver": foregroundReceiver,
                "desktop": final, "foregroundChanged": Self.foregroundChanged(initial, final),
                "durationMs": max(0, clock() - started) * 1000
            ])
    }

    private static func foregroundChanged(_ before: [String: Any], _ after: [String: Any]) -> Bool {
        guard let first = before["foregroundPID"] as? Int32, let last = after["foregroundPID"] as? Int32 else {
            return false
        }
        return first != last
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
