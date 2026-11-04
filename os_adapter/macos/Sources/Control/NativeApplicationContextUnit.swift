// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices

@MainActor protocol NativeApplicationContextInput {
    func prepare(
        requirement: NativeApplicationContextUnit.Requirement,
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
    enum Requirement { case optional, activeWhenKnownInactive }
    let application: NSRunningApplication

    func prepare(
        requirement: Requirement,
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
        let outcome = try await NativeForegroundRestorationScope.perform(
            target: target, onPreparation: onPreparation, onCleanupUnknown: onCleanupUnknown
        ) {
          try await Self.prepare(
            pid: application.processIdentifier, requirement: requirement,
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
            wait: { try await Task.sleep(for: .milliseconds(5)) })
        }
        if requirement == .activeWhenKnownInactive, outcome == .activityObserved {
            // Returning the real foreground is an actual state boundary. Do not
            // reuse an active sample that preceded a required restitution.
            try check()
            try target.validate(input: true)
            if AXAccess.read(root, kAXFrontmostAttribute).value as? Bool == false { return .posted }
        }
        return outcome
    }

    /// Optional construction and provider observations never authorize a new
    /// foreground route or prove input readiness.
    static func prepare(
        pid: pid_t, requirement: Requirement = .optional,
        sample: () throws -> State, alreadyPosted: () -> Bool, make: () -> CGEvent?,
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
        if requirement == .activeWhenKnownInactive {
            if initial.reportedActive == true { return try finish(.activityObserved) }
            if initial.reportedActive == nil { return try finish(.skipped) }
        } else {
            guard let front = initial.foregroundPID, front != pid, initial.reportedActive != true else {
                return try finish(.skipped)
            }
        }
        if initial.reportedActive == nil && alreadyPosted() { return try finish(.skipped) }
        guard let event = make() else { return try finish(.unavailable) }
        try check()
        let detail = "process-local application activation notification"
        onPreparation(detail)
        onNativePreparation(.init(kind: .focusNotification, phase: .willSend, detail: detail, window: nil))
        post(event)
        onNativePreparation(.init(kind: .focusNotification, phase: .returned, detail: detail, window: nil))
        // At most two queue turns under the original budget. Ordinary optional
        // context never becomes a readiness gate; a proven inactive Qt opener
        // separately consumes the observed outcome before dispatch.
        for attempt in 0..<3 {
            try pollCheck()
            let current = try sample()
            try pollCheck()
            if current.reportedActive == true { return try finish(.activityObserved) }
            // A missing provider report cannot turn optional context into a gate.
            if current.reportedActive == nil || requirement == .optional && current.foregroundPID == pid {
                return try finish(.posted)
            }
            if attempt < 2 { try await wait() }
        }
        return try finish(.posted)
    }
}
