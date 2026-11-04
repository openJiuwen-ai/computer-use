// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices

/// Establish process/window input context inside the selected native operation.
@MainActor enum NativeFocusPipeline {
    @discardableResult static func prepare(
        _ target: BackgroundTarget, window: WindowGeometry.WindowMapping, nativeWindow: AXUIElement? = nil,
        needsKeyWindow: Bool = true, onPreparation: @escaping @MainActor (String) -> Void = { _ in },
        onCleanupUnknown: @escaping @MainActor () -> Void = {},
        beforeAction: @escaping @MainActor () throws -> Void = {},
        onNativePreparation: @escaping @MainActor (NativePreparationEffect) -> Void = { _ in },
        observeOutcome: Bool = false
    ) async throws -> NativeFocusOutcome {
        let request = NativeFocusRequest(
            target: target, window: window, element: nativeWindow, needsKeyWindow: needsKeyWindow, check: beforeAction,
            onPreparation: onPreparation, onCleanupUnknown: onCleanupUnknown,
            onNativePreparation: { event in
                NativeForegroundRestorationScope.current?.record(event)
                onNativePreparation(event)
            })
        let metric = RequestDiagnostics.metrics?.start(.windowPreparation)
        defer { metric?.end() }
        return try await NativeForegroundRestorationScope.perform(
            target: target, onPreparation: onPreparation,
            onCleanupUnknown: onCleanupUnknown
        ) {
          try await run(
            request, observeOutcome: observeOutcome,
            stages: NativeFocusStages(
                begin: {
                    try BackgroundEvents.requireSyntheticFocusRoute(window: $0.window)
                    SyntheticFocusCoordinator.shared.observe($0.window.pid)
                }, applicationContext: PrepareFocusApplicationContextPass.run, sample: SampleFocusStatePass.run,
                notify: PrepareSyntheticApplicationPass.run, selectWindow: PrepareTargetWindowPass.run,
                settle: { _ in try await Task.sleep(for: .milliseconds(30)) }, confirm: ConfirmFocusPreparationPass.run,
                finishPreparation: { _ in try await NativeForegroundRestorationScope.current?.finish() ?? false })
          )
        }
    }

    /// The same selected-window composition is used by the native adapter and
    /// mechanism regressions; no injected Plan or second preparation route.
    static func run(_ request: NativeFocusRequest, observeOutcome: Bool, stages: NativeFocusStages) async throws
        -> NativeFocusOutcome
    {
        try stages.begin(request)
        try await stages.applicationContext(request)
        let state = try stages.sample(request)
        let plan = PlanFocusPreparationPass.run(state, window: request.window.id, needsKeyWindow: request.needsKeyWindow)
        if plan.notify { try stages.notify(request) }
        if plan.selectWindow { try stages.selectWindow(request) }
        // Keep the existing bounded queue settle. The key-focus notification
        // neither requests an active ACK nor proves the window/receiver ready.
        if plan.notify || plan.selectWindow { try await stages.settle(request) }
        // Foreground restitution belongs before confirmation/input. Its real
        // deactivation may change the key/main receiver and invalidate receipts.
        let restored = try await stages.finishPreparation(request)
        var restoredState = restored ? try stages.sample(request) : nil
        if let state = restoredState, !state.foreground && state.reportedActive == false {
            // Returning the user's foreground can deactivate the target after
            // selection. Reestablish local activity once, then use fresh facts:
            // its notification may itself change the app's key window.
            try await stages.applicationContext(request)
            restoredState = try stages.sample(request)
        }
        try stages.confirm(request)
        return NativeFocusOutcome(
            initial: state, final: observeOutcome ? try stages.sample(request) : restoredState ?? state,
            waitEnd: .notNeeded)
    }
}
