import AppKit
import ApplicationServices

/// QUERY: construct only this selected native route; PREPARE/DISPATCH is deferred to its returned closure.
@MainActor enum BuildNativePointerFocusRoutePass {
    static func run(_ context: NativeRouteContext) throws -> SelectedNativeRoute {
        let contract = context.resolved.contract
        let op = context.operation
        let bound = context.command.target
        let kind = context.command.kind
        let intentKind = context.operation.request.kind
        let foreground = context.command.foreground
        let id = context.command.primitiveID
        let receipt = context.receipt
        let checkBoundary = context.boundary.checkConditions

        guard kind == "prepare_receiver", ["focus", "insert", "replace", "paste"].contains(intentKind) else {
            throw DriverError("pointer focus is preparation only", code: .invalidRequest)
        }
        let selected = try context.access.units.selectedTextUnit(
            bound, actuator: contract.actuator, receiver: contract.receiver)
        let unit = NativePointerFocusUnit(
            application: selected.application, window: selected.window, element: selected.element,
            receiverRelation: selected.receiverRelation)
        return .prepare {
            if foreground {
                try op.requireForegroundPreparation(
                    id, hostAllowed: ForegroundAssist.enabled || BackgroundTarget.isExclusive)
            }
            try await unit.prepare(
                foreground: foreground, check: checkBoundary, onPreparation: receipt.recordPreparation,
                onCleanupUnknown: receipt.recordCleanupUnknown)
            receipt.detail = "Editor pointer focus prepared; no requested text or key was sent."
            receipt.confirmation = "confirmed"
        }

    }
}
