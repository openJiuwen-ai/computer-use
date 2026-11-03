// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices

/// Explicit PREPARE before observation. Never an inspect/capture side effect,
/// input-receiver recipe, mouse primer, or promise of synchronized application redraw.
@MainActor enum ObservationPreparationPipeline {
    static func run(_ raw: Any?, operation: (Any?) throws -> NativeOperation, scopes: NativeScopeResolver)
        async throws -> [String: Any]
    {
        let request = try object(raw, keys: ["operation", "target"])
        let target = try NativeTarget(request["target"]).wire
        let op = try operation(request["operation"])
        guard !op.writable, NSDictionary(dictionary: op.target).isEqual(to: target),
            target["node"] is NSNull, target["capture"] is NSNull
        else { throw DriverError("observation preparation requires its original window read scope", code: .invalidRequest) }
        for field in ["instance", "rootWindow", "surface"] { _ = try string(target[field], label: field) }
        var attempted = false
        let activity = NativeInputActivity(
            operation: op.token, step: 0, command: "prepareObservation", intent: "observe", target: target,
            globalInputAllowed: false)
        defer { activity.finish(dispatch: attempted ? "dispatched" : "notDispatched", cleanup: op.cleanup) }
        do {
            return try await NativeInputActivity.$current.withValue(activity) {
                try await NativeInputActivity.$stage.withValue(.preparation) {
                  try await op.observe {
                    let original = try scopes.observationSurface(target)
                    let background = try BackgroundTarget(original.native?.application ?? original.owner)
                    let check: @MainActor @Sendable () throws -> Void = {
                        try op.check()
                        try RequestDiagnostics.checkCancellation()
                        try background.validate(input: false)
                        _ = try scopes.observationSurface(target)
                    }
                    try check()
                    var activation = "notRequested"
                    if !background.isForeground {
                        let receipt = try await ForegroundActivation.request(
                            background, check: check, onCleanupUnknown: { op.recordUnconfirmedCleanup() })
                        attempted = receipt.invoked
                        activation = receipt.invoked ? "requested" : "notRequested"
                    }
                    try check()
                    var raised = "notRequested"
                    if let window = original.element {
                        let status = AXMessaging.performAction(window, kAXRaiseAction as CFString, onCall: { attempted = true })
                        raised = status == .success ? "requested" : status == .actionUnsupported ? "unsupported" : "unknown"
                        if status == .actionUnsupported, let native = original.native {
                            try check()
                            attempted = true
                            try BackgroundEvents.selectForegroundWindow(native.mapping.id)
                            raised = "requested"
                        }
                    } else if let native = original.native {
                        // Weak AX still has an exact WindowServer identity. This
                        // existing adapter selects that window without mouse input.
                        attempted = true
                        try BackgroundEvents.selectForegroundWindow(native.mapping.id)
                        raised = "requested"
                    }
                    // A bounded opportunity for the app to process activation. This is
                    // neither a keyboard-readiness gate nor evidence that its pixels changed.
                    if attempted { try await Task.sleep(for: .milliseconds(60)) }
                    try check()
                    return [
                        "kind": "observationPreparation", "binding": target, "activation": activation,
                        "raiseStatus": raised, "foreground": background.isForeground, "redraw": "unknown"
                    ]
                  }
                }
            }
        } catch {
            if let effect = (error as? DriverError)?.effect, effect != .none { attempted = true }
            guard attempted else { throw error }
            throw DriverError(
                "window observation preparation may have changed focus or stacking: \(error)",
                code: (error as? ClassifiedFailure)?.code ?? .executionFailed, effect: .possible,
                next: "observe the original window without activation; do not repeat preparation blindly")
        }
    }
}
