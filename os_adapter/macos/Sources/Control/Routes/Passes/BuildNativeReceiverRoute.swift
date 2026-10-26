import AppKit
import ApplicationServices

/// QUERY: construct only this selected native route; PREPARE/DISPATCH is deferred to its returned closure.
@MainActor enum BuildNativeReceiverRoutePass {
    static func run(_ context: NativeRouteContext, receiverContract: NativeReceiverPreparation.Contract) throws
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

        guard kind == "prepare_receiver", ["insert", "replace", "paste"].contains(intentKind) else {
            throw DriverError("native receiver preparation does not authorize input", code: .invalidRequest)
        }
        if intentKind == "paste" {
            _ = try NativeKeyboardIntent(
                unit: "clipboard_paste", kind: intentKind, parameters: op.request.parameters, foreground: false)
        } else {
            _ = try NativeObjectIntent(unit: "native_text_edit", kind: intentKind, parameters: op.request.parameters)
        }
        let selected = try context.access.units.selectedTextUnit(
            bound, actuator: contract.actuator, receiver: contract.receiver)
        let unit = NativeReceiverPreparation(
            application: selected.application, window: selected.window, element: selected.element,
            receiverRelation: selected.receiverRelation)
        return .prepare {
            if foreground {
                try op.requireForegroundPreparation(
                    id, hostAllowed: ForegroundAssist.enabled || BackgroundTarget.isExclusive)
            }
            try await unit.prepare(
                foreground: foreground, check: { try unit.validate(receiverContract, check: checkBoundary) },
                ready: { try unit.readiness().ready }, onPreparation: receipt.recordPreparation,
                onCleanupUnknown: receipt.recordCleanupUnknown)
            receipt.detail = "Native receiver context confirmed; inspect editing capability before sending text."
            receipt.confirmation = "confirmed"
        }

    }
}
