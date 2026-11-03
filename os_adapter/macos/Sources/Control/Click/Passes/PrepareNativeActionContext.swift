// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices

/// PREPARE: local activity only for a proven original-document selection need.
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
        // An AXPress declaration does not establish an activation dependency.
        // An AppKit activation notification can make a provider truly foreground
        // and change popup lifetime, even though it was addressed to its PID.
        // Only the independent document preparation may need this context.
        guard preparingDocument else { return }
        func validateAction() throws {
            try boundary.checkConditions()
            let facts = try objects.nativeActionFacts(node)
            guard NativeAction.matches(facts.actions, name: name, description: description) else {
                throw DriverError("the selected action changed during local-context preparation", code: .staleTarget)
            }
            try boundary.checkConditions()
        }
        try validateAction()
        let outcome = try await unit.prepare(
            check: validateAction, onPreparation: receipt.recordPreparation, onNativePreparation: evidence.record)
        try validateAction()
        evidence.observeActionContext(outcome)
    }
}
