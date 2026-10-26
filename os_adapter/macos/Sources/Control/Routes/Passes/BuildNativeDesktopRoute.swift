import AppKit
import ApplicationServices

/// QUERY: construct only this selected native route; PREPARE/DISPATCH is deferred to its returned closure.
@MainActor enum BuildNativeDesktopRoutePass {
    static func run(_ context: NativeRouteContext, expected: String) throws -> SelectedNativeRoute {
        let op = context.operation
        let bound = context.command.target
        let kind = context.command.kind
        let intentKind = context.operation.request.kind
        let foreground = context.command.foreground
        let receipt = context.receipt
        let checkBoundary = context.boundary.checkConditions
        let markDispatch = context.boundary.markDispatchInFlight

        return .dispatch {
            guard expected == intentKind, !foreground else {
                throw DriverError("desktop contract intent changed", code: .invalidRequest)
            }
            let unit = try context.access.units.desktopUnit(bound)
            if NativePointerIntent.Kind(rawValue: expected) != nil {
                guard kind == "pointer_sequence" else {
                    throw DriverError("desktop pointer contract mismatch", code: .invalidRequest)
                }
                let request = try NativePointerIntent(
                    unit: kind, kind: intentKind, parameters: op.request.parameters, foreground: false)
                try await unit.deliver(
                    request, check: checkBoundary, onDispatch: markDispatch, onPosted: receipt.recordAccepted,
                    onCleanupUnknown: receipt.recordCleanupUnknown)
            } else {
                guard kind == "key_sequence" else {
                    throw DriverError("desktop keyboard contract mismatch", code: .invalidRequest)
                }
                let request = try NativeKeyboardIntent(
                    unit: kind, kind: intentKind, parameters: op.request.parameters, foreground: false)
                try await unit.deliver(
                    request, check: checkBoundary, onDispatch: markDispatch, onPosted: receipt.recordAccepted,
                    onCleanupUnknown: receipt.recordCleanupUnknown)
            }
            receipt.detail =
                intentKind == "scroll"
                ? """
                Desktop wheel input dispatched; pages approximate the visible window length (display length \
                if no window); content movement unconfirmed.
                """ : "Desktop \(intentKind) dispatched; application effect unconfirmed."
            receipt.context = ["after": unit.ref, "scope": context.access.desktop().scope, "window": NSNull()]
            return false
        }

    }
}
