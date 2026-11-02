// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices

/// PREPARE: optional local activity for the original action, with no requested input.
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
        } else {
            preparingDocument = false
        }
        func validateAction() throws {
            try boundary.checkConditions()
            let facts = try objects.nativeActionFacts(node)
            guard NativeAction.matches(facts.actions, name: name, description: description) else {
                throw DriverError("the selected action changed during local-context preparation", code: .staleTarget)
            }
            guard preparingDocument || facts.enabled != false else {
                throw DriverError("the selected action became disabled", code: .inputUnavailable)
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
