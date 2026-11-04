// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices

/// Explicit object-action composition: optional application activity, exact
/// document preparation, then one addressed action inside its final boundary.
@MainActor enum NativeObjectActionPipeline {
    static func select(_ context: NativeRouteContext, name: String, description: String?) throws -> SelectedNativeRoute
    {
        let binding = context.resolved.binding
        let contract = context.resolved.contract
        let op = context.operation
        let kind = context.command.kind
        let plan = try ResolveNativeObjectActionPass.run(
            unit: kind, intent: op.request, node: binding.node, action: name, description: description)
        let request = plan.request, node = plan.node

        @MainActor func facts() throws -> NativeElementControl.ActionFacts {
            if request.kind == "action" {
                return try context.access.objects.nativeExplicitActionFacts(node, request: request.value!)
            }
            return try context.access.objects.nativeActionFacts(node)
        }
        let documentScope = NativeActionDocumentContexts.Scope(
            node: node, surface: try string(binding.wire["surface"], label: "surface"), within: contract.within,
            actuator: contract.actuator, receiver: contract.receiver)
        @MainActor func originalDocument() throws -> NativeActionDocumentContract? {
            guard name == kAXPressAction else { return nil }
            if let retained = contract.actionDocument { return retained }
            return try op.actionDocuments.context(documentScope) { try facts().document }
        }
        @MainActor func checkActionDeclaration() throws -> NativeElementControl.ActionFacts {
            try context.boundary.checkConditions()
            let current = try facts()
            try context.boundary.checkConditions()
            let matches =
                request.kind == "action"
                ? current.actions?.count == 1 && current.actions?.first?.name == name
                : NativeAction.matches(current.actions, name: name, description: description)
            guard matches else { throw DriverError("the selected native action changed", code: .staleTarget) }
            return current
        }
        @MainActor func checkPreparationAction() throws { _ = try checkActionDeclaration() }
        @MainActor func checkReadyAction() throws {
            let current = try checkActionDeclaration()
            guard current.enabled != false else {
                throw DriverError("the selected action became disabled", code: .inputUnavailable)
            }
        }
        @MainActor func prepareLocalContext(_ check: @escaping @MainActor () throws -> Void) async throws {
            if let local = try context.access.units.actionContextUnit(context.command.target, action: name) {
                try check()
                _ = try await local.prepare(
                    check: check, onPreparation: context.receipt.recordPreparation, onNativePreparation: { _ in })
                try check()
            }
        }
        if kind == "prepare_receiver" {
            return .prepare {
                // This is the existing operation-owned authority: a real
                // background failure must precede any foreground request.
                if context.command.foreground { try op.requireForegroundPreparation(context.command.primitiveID) }
                try checkPreparationAction()
                guard let retained = try originalDocument(), retained.selectionRequired else {
                    throw DriverError(
                        "this object action has no known original document context to prepare", code: .inputUnavailable)
                }
                if try retained.observe() != .target {
                    // Window selection is admitted independently from whether
                    // its controls are currently enabled. Every preparation
                    // still checks the original object, action and scope.
                    try await prepareLocalContext(checkPreparationAction)
                    try await NativeDocumentActionPreparationPipeline.run(
                        retained.unit, foreground: context.command.foreground,
                        authorizeForeground: { try op.requireForegroundPreparation(context.command.primitiveID) },
                        check: checkPreparationAction, onPreparation: context.receipt.recordPreparation,
                        onCleanupUnknown: context.receipt.recordCleanupUnknown, onNativePreparation: { _ in },
                        onWait: { _ in })
                } else {
                    try checkReadyAction()
                }
                context.receipt.detail = "Original document action context prepared; no requested action was sent."
                context.receipt.confirmation = "confirmed"
            }
        }
        return .dispatch {
            try await NativeInputActivity.$stage.withValue(.preparation) {
                try await prepareLocalContext(checkReadyAction)
            }
            if let retained = try originalDocument(), try retained.observe() == .other,
                !op.preparationConfirmed(context.command.primitiveID)
            {
                // Optional local activity may restore a different key window.
                // Re-observe the same contract before selecting preparation;
                // no requested action has entered its dispatch barrier.
                throw NativeSelectionBasis.Changed()
            }
            @MainActor func actionBoundary() throws {
                try context.boundary.checkConditions()
                if name == kAXPressAction {
                    let retained = try originalDocument()
                    try NativeActionDocumentPolicy.checkDispatch(
                        retained?.unit, required: retained?.selectionRequired == true, retained: retained)
                }
                try context.boundary.beforeDispatch()
            }
            if request.kind == "action" {
                try context.access.objects.executeNativeExplicitAction(
                    node, request: request.value!, action: name, onDispatch: actionBoundary, onReturned: {})
            } else {
                try context.access.objects.executeNativeAction(
                    node, action: name, description: description, onDispatch: actionBoundary)
            }
            return false
        }
    }
}
