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
            onPreparation: onPreparation, onCleanupUnknown: onCleanupUnknown, onNativePreparation: onNativePreparation)
        let metric = RequestDiagnostics.metrics?.start(.windowPreparation)
        defer { metric?.end() }
        try BackgroundEvents.requireSyntheticFocusRoute(window: window)
        SyntheticFocusCoordinator.shared.observe(window.pid)
        let state = try SampleFocusStatePass.run(request)
        let plan = PlanFocusPreparationPass.run(state, window: window.id)
        if plan.notify { try PrepareSyntheticApplicationPass.run(request) }
        if plan.selectWindow { try PrepareTargetWindowPass.run(request) }
        // Keep the existing bounded queue settle. The key-focus notification
        // neither requests an active ACK nor proves the window/receiver ready.
        if plan.notify || plan.selectWindow { try await Task.sleep(for: .milliseconds(30)) }
        try ConfirmFocusPreparationPass.run(request)
        return NativeFocusOutcome(
            initial: state, final: observeOutcome ? try SampleFocusStatePass.run(request) : state, waitEnd: .notNeeded)
    }
}
