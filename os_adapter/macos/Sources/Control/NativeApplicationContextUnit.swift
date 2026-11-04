// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices

@MainActor protocol NativeApplicationContextInput {
    func prepare(
        check: @escaping @MainActor () throws -> Void, onPreparation: @escaping @MainActor (String) -> Void,
        onCleanupUnknown: @escaping @MainActor () -> Void,
        onNativePreparation: @escaping @MainActor (NativePreparationEffect) -> Void
    ) async throws -> NativeApplicationContextUnit.Outcome
}

/// Optional process-local application activity. Provider activity, actual
/// foreground and keyboard focus remain distinct.
@MainActor struct NativeApplicationContextUnit: NativeApplicationContextInput {
    struct State {
        var foregroundPID: pid_t?
        var reportedActive: Bool?
    }
    enum Outcome: String { case skipped, unavailable, posted, activityObserved }
    let application: NSRunningApplication

    func prepare(
        check: @escaping @MainActor () throws -> Void, onPreparation: @escaping @MainActor (String) -> Void,
        onCleanupUnknown: @escaping @MainActor () -> Void,
        onNativePreparation: @escaping @MainActor (NativePreparationEffect) -> Void
    ) async throws -> Outcome {
        let target = BackgroundTarget(application)
        try check()
        try target.validate(input: true)
        guard let process = target.capturedProcessIdentity else { return .unavailable }
        let root = AXUIElementCreateApplication(application.processIdentifier)
        AXUIElementSetMessagingTimeout(root, 0.1)
        let coordinator = SyntheticFocusCoordinator.shared
        return try await NativeForegroundRestorationScope.perform(
            target: target, check: check, onPreparation: onPreparation, onCleanupUnknown: onCleanupUnknown
        ) {
          try await Self.prepare(
            pid: application.processIdentifier,
            sample: {
                State(
                    foregroundPID: NSWorkspace.shared.frontmostApplication?.processIdentifier,
                    reportedActive: AXAccess.read(root, kAXFrontmostAttribute).value as? Bool)
            }, alreadyPosted: { coordinator.localActivationIsCurrent(target) },
            make: {
                guard let event = SyntheticFocusEvents.applicationActivation() else { return nil }
                coordinator.observe(application.processIdentifier)
                return event
            },
            post: { event in
                let activity = NativeInputActivity.begin(
                    device: .focus, transport: .process, mechanism: "processApplicationActivationNotification",
                    pid: application.processIdentifier, event: event, stage: .preparation)
                event.postToPid(application.processIdentifier)
                coordinator.recordLocalActivationPosted(target, process: process)
                activity?.returned()
            },
            check: {
                try check()
                try target.validate(input: true)
            },
            pollCheck: {
                try RequestDiagnostics.checkCancellation()
                guard NativeProcessIdentity.read(application.processIdentifier) == process else {
                    throw DriverError(
                        "the original action host changed during local activity observation", code: .staleTarget)
                }
            }, onPreparation: onPreparation, onNativePreparation: { event in
                NativeForegroundRestorationScope.current?.record(event)
                onNativePreparation(event)
            },
            wait: { try await Task.sleep(for: .milliseconds(25)) })
        }
    }

    /// Optional construction and provider observations never authorize a new
    /// foreground route or prove input readiness.
    static func prepare(
        pid: pid_t, sample: () throws -> State, alreadyPosted: () -> Bool, make: () -> CGEvent?,
        post: (CGEvent) -> Void, check: () throws -> Void, pollCheck: () throws -> Void = {},
        onPreparation: (String) -> Void, onNativePreparation: (NativePreparationEffect) -> Void,
        wait: () async throws -> Void
    ) async throws -> Outcome {
        func finish(_ outcome: Outcome) throws -> Outcome {
            try check()
            return outcome
        }
        try check()
        let initial = try sample()
        try check()
        guard let front = initial.foregroundPID, front != pid, initial.reportedActive != true else {
            return try finish(.skipped)
        }
        if initial.reportedActive == nil && alreadyPosted() { return try finish(.skipped) }
        guard let event = make() else { return try finish(.unavailable) }
        try check()
        let detail = "process-local application activation notification"
        onPreparation(detail)
        onNativePreparation(.init(kind: .focusNotification, phase: .willSend, detail: detail, window: nil))
        post(event)
        onNativePreparation(.init(kind: .focusNotification, phase: .returned, detail: detail, window: nil))
        // Keep a bounded opportunity for the target's activity to settle.
        // Pointer readiness may consume this fact after foreground restitution;
        // returning before a slow acknowledgement would trigger needless fallback.
        for attempt in 0..<12 {
            try pollCheck()
            let current = try sample()
            try pollCheck()
            if current.reportedActive == true { return try finish(.activityObserved) }
            // A missing provider report cannot turn optional context into a gate.
            if current.reportedActive == nil || current.foregroundPID == pid { return try finish(.posted) }
            if attempt < 11 { try await wait() }
        }
        return try finish(.posted)
    }
}
