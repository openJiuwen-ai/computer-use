// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices

/// Explicit PREPARE before observation. Never an inspect/capture side effect,
/// input-receiver recipe, mouse primer, or promise of synchronized application redraw.
@MainActor enum ObservationPreparationPipeline {
    static func run(
        _ raw: Any?, operation: (Any?) throws -> NativeOperation, scopes: NativeScopeResolver, launch: NativeLaunchAccess
    )
        async throws -> [String: Any]
    {
        let request = try object(raw, keys: ["operation", "target"])
        let op = try operation(request["operation"])
        if let application = request["target"] as? [String: Any], application["application"] != nil {
            return try await reopen(application, op: op, scopes: scopes, launch: launch)
        }
        let target = try NativeTarget(request["target"]).wire
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

    private static func reopen(
        _ raw: [String: Any], op: NativeOperation, scopes: NativeScopeResolver, launch: NativeLaunchAccess
    ) async throws -> [String: Any] {
        let target = try object(raw, keys: ["session", "application"])
        let session = try string(target["session"], label: "session")
        let application = try string(target["application"], label: "application")
        let discoveryScope = try NativeTarget(op.target)
        try scopes.requireSession(session)
        try discoveryScope.requireSession(session)
        guard !op.writable, discoveryScope.isDiscovery,
            ApplicationInstances<NSRunningApplication>.isInstanceSelector(application)
        else {
            throw DriverError("application reopen requires an observed running app and a read scope", code: .invalidRequest)
        }

        var dispatch = "notDispatched"
        let activity = NativeInputActivity(
            operation: op.token, step: 0, command: "prepareObservation", intent: "observe", target: target,
            globalInputAllowed: false)
        defer { activity.finish(dispatch: dispatch, cleanup: op.cleanup) }
        do {
            return try await NativeInputActivity.$current.withValue(activity) {
                try await NativeInputActivity.$stage.withValue(.preparation) {
                    try await op.observe {
                        try scopes.sharing.checkAvailable()
                        guard case .running(let app) = try launch.discovery.locate(application),
                            let url = app.bundleURL?.standardizedFileURL.resolvingSymlinksInPath()
                        else {
                            throw DriverError("the original running application has no reopenable bundle", code: .unsupportedOperation)
                        }
                        let background = BackgroundTarget(app)
                        let workspace = NSWorkspace.shared
                        // LaunchServices targets a bundle URL. Prove which running
                        // process that URL addresses before sending the request.
                        let matches = workspace.runningApplications.filter {
                            !$0.isTerminated && $0.bundleURL?.standardizedFileURL.resolvingSymlinksInPath() == url
                        }
                        guard matches.count == 1, matches[0].isEqual(app) else {
                            throw DriverError("the original running application is not the unique reopen target", code: .ambiguousTarget)
                        }
                        let configuration = NativeLaunchUnit.openConfiguration(activates: true)
                        try background.validate()
                        try op.check()
                        try RequestDiagnostics.checkCancellation()
                        dispatch = "unknown"
                        launch.recordEffect()
                        let attempt = NativeInputActivity.begin(
                            device: .application, transport: .launch, mechanism: "NSWorkspace.openApplication")
                        defer { attempt?.returned() }
                        let opened = try await workspace.openApplication(at: url, configuration: configuration)
                        dispatch = "dispatched"
                        guard opened.isEqual(app) else {
                            throw DriverError("application reopen returned a different process", code: .staleTarget)
                        }
                        try background.validate()
                        return [
                            "kind": "observationPreparation", "binding": target, "activation": "requested",
                            "raiseStatus": "notRequested", "reopenStatus": "requested",
                            "foreground": background.isForeground, "redraw": "unknown"
                        ]
                    }
                }
            }
        } catch {
            guard dispatch != "notDispatched" else { throw error }
            throw DriverError(
                "application reopen may have changed the desktop: \(error)",
                code: (error as? ClassifiedFailure)?.code ?? .executionFailed, effect: .possible,
                next: "observe the original application to check whether a window appeared")
        }
    }
}
