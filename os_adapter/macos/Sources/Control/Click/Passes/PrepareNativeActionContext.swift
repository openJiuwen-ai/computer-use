// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices

/// PREPARE: local activity for an independent document need or a proven Qt
/// popup opener. Ordinary AX declarations do not inherit either requirement.
@MainActor enum PrepareNativeActionContextPass {
    static func run(
        _ unit: any NativeApplicationContextInput, plan: NativeClickPlan, boundary: NativeUnitBoundary,
        receipt: NativeExecutionReceipt, evidence: NativeClickEvidence, objects: any NativeControlObjectAccess
    ) async throws {
        guard case .action(let node, let name, let description) = plan.target else { return }
        let preparingDocument: Bool
        if let retained = plan.actionDocument {
            let focus = try retained.observe()
            preparingDocument = retained.selectionRequired && focus != .target
        } else if plan.documentPreparation.windowSelectionRequired, let document = plan.document {
            preparingDocument = try document.focus() != .target
        } else {
            preparingDocument = false
        }
        let opener = plan.qtPopupOpener
        guard preparingDocument || opener != nil else { return }
        let validateAction: @MainActor () throws -> Void = {
            try boundary.checkConditions()
            let facts = try objects.nativeActionFacts(node)
            guard NativeAction.matches(facts.actions, name: name, description: description) else {
                throw DriverError("the selected action changed during local-context preparation", code: .staleTarget)
            }
            if let opener, facts.qtPopupOpener != opener {
                throw DriverError("the original Qt popup opener changed during preparation", code: .staleTarget)
            }
            if opener != nil, !preparingDocument, facts.enabled == false {
                throw DriverError("the Qt popup opener became disabled", code: .inputUnavailable)
            }
            try boundary.checkConditions()
        }
        try validateAction()
        let outcome = try await unit.prepare(
            requirement: opener == nil ? .optional : .activeWhenKnownInactive,
            check: validateAction, onPreparation: receipt.recordPreparation,
            onCleanupUnknown: receipt.recordCleanupUnknown, onNativePreparation: evidence.record)
        try validateAction()
        evidence.observeActionContext(outcome)
        if opener != nil { try NativeQtPopupOpener.requirePrepared(outcome) }
    }
}
