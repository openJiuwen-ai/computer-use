import AppKit
import ApplicationServices

/// QUERY: construct only this selected native route; PREPARE/DISPATCH is deferred to its returned closure.
@MainActor enum BuildNativeSelectedTextRoutePass {
    static func run(_ context: NativeRouteContext, editContract: NativeSelectedTextUnit.Contract) throws
        -> SelectedNativeRoute
    {
        let contract = context.resolved.contract
        let op = context.operation
        let bound = context.command.target
        let kind = context.command.kind
        let intentKind = context.operation.request.kind
        let foreground = context.command.foreground
        let id = context.command.primitiveID
        let receipt = context.receipt
        let checkBoundary = context.boundary.checkConditions
        let checkSelection = context.boundary.checkSelection
        let markDispatch = context.boundary.markDispatchInFlight

        let request = try NativeObjectIntent(
            unit: "native_text_edit", kind: intentKind, parameters: op.request.parameters)
        guard ["prepare_receiver", "native_text_edit"].contains(kind), let text = request.value,
            editContract.replace == (intentKind == "replace")
        else { throw DriverError("native selected-text contract mismatch", code: .invalidRequest) }
        let unit = try context.access.units.selectedTextUnit(
            bound, actuator: contract.actuator, receiver: contract.receiver)
        if kind == "prepare_receiver" {
            return .prepare {
                if foreground {
                    try op.requireForegroundPreparation(
                        id, hostAllowed: ForegroundAssist.enabled || BackgroundTarget.isExclusive)
                }
                try await unit.prepare(
                    editContract, foreground: foreground, check: checkBoundary,
                    onPreparation: receipt.recordPreparation, onCleanupUnknown: receipt.recordCleanupUnknown)
                receipt.detail = "Native editor context confirmed; no text was sent."
                receipt.confirmation = "confirmed"
            }
        }
        return .dispatch {
            let alreadySatisfied = try await unit.deliver(
                editContract, text: text, check: checkBoundary,
                // A selection setter can change the composed
                // control before the separate content setter.
                // Recheck the chosen semantics at both writes,
                // even after the operation has recorded effects.
                onDispatch: {
                    if receipt.dispatch != "notDispatched" { try checkSelection() }
                    try markDispatch()
                }, onPosted: receipt.recordAccepted)
            receipt.detail =
                alreadySatisfied
                ? (intentKind == "replace"
                    ? "The complete target text is already empty; no replacement input was sent."
                    : "Empty text; no input was sent.")
                : "Native selected-text edit dispatched; application effect unconfirmed."
            if alreadySatisfied { receipt.confirmation = "confirmed" }
            return alreadySatisfied
        }

    }
}
