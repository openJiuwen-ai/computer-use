// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices

/// Explicit preparation composition for the fixed selected delivery. No
/// attempt loop, cross-delivery selection or fresh request deadline lives here.
@MainActor enum NativeClickPreparationPipeline {
    /// A route sampled as ready may lose foreground before execution. Its
    /// retained requirement still excludes local background preparation; the
    /// existing Gate owns any later explicitly selected foreground attempt.
    static func requireBackgroundPreparation(_ requirement: NativePointerReadiness.Requirement) throws {
        guard requirement != .foregroundWindowPoint else {
            throw NativePreparationUnavailable(
                description: "the original point needs foreground preparation; no background preparation was sent")
        }
    }

    static func run(
        _ plan: NativeClickPlan, boundary: NativeUnitBoundary, receipt: NativeExecutionReceipt,
        evidence: NativeClickEvidence, objects: any NativeControlObjectAccess
    ) async throws {
        let metric = RequestDiagnostics.metrics?.start(.clickPreparation)
        defer { metric?.end() }
        func checkDocumentAction() throws {
            try boundary.checkConditions()
            if case .action(let node, let name, let description) = plan.target {
                let facts = try objects.nativeActionFacts(node)
                guard NativeAction.matches(facts.actions, name: name, description: description) else {
                    throw DriverError("the selected action changed during document preparation", code: .staleTarget)
                }
            }
            try boundary.checkConditions()
        }
        if let context = plan.actionContext {
            try await PrepareNativeActionContextPass.run(
                context, plan: plan, boundary: boundary, receipt: receipt, evidence: evidence, objects: objects)
        }
        // Local application activity can restore another key/main document.
        // Record it before the shared document preparation checks current focus.
        if let retained = plan.actionDocument { try retained.observe() }
        if let document = plan.document,
            plan.documentPreparation.windowSelectionRequired || plan.actionDocument?.selectionRequired == true
        {
            try await NativeDocumentActionPreparationPipeline.run(
                document, foreground: plan.command.foreground,
                authorizeForeground: { try AuthorizeNativeClickForegroundPass.run(plan) }, check: checkDocumentAction,
                onPreparation: receipt.recordPreparation, onCleanupUnknown: receipt.recordCleanupUnknown,
                onNativePreparation: evidence.record, onWait: evidence.recordWait)
        }
        if case .menu(let unit, _, _, _) = plan.target {
            let foreground = plan.command.foreground && !BackgroundTarget(unit.application).isForeground
            if foreground { try AuthorizeNativeClickForegroundPass.run(plan) }
            // The existing menu preparation owns a composed native focus/menu
            // sequence. Keep that composition visible at the parent pipeline.
            try await unit.prepare(
                foreground: foreground, check: boundary.checkConditions, onPreparation: receipt.recordPreparation,
                onCleanupUnknown: receipt.recordCleanupUnknown, onNativePreparation: evidence.record,
                onFocusOutcome: evidence.observe, onPreparationWait: evidence.recordWait, observeOutcome: true)
        }
        if let preparation = try SampleNativeClickPreparationPass.run(plan, boundary: boundary), preparation.needed {
            switch plan.offer.delivery {
            case .windowDirected where plan.command.foreground:
                try AuthorizeNativeClickForegroundPass.run(plan)
                try await PrepareNativeClickForegroundPass.run(
                    preparation, boundary: boundary, receipt: receipt, evidence: evidence)
            case .windowDirected:
                let pointer = preparation.pointer
                try requireBackgroundPreparation(pointer.pointerRequirement)
                try pointer.validateWindowIdentity()
                do {
                    let outcome = try await NativeFocusPipeline.prepare(
                        BackgroundTarget(pointer.application), window: pointer.window,
                        nativeWindow: pointer.nativeWindow, onPreparation: receipt.recordPreparation,
                        onCleanupUnknown: receipt.recordCleanupUnknown, beforeAction: boundary.checkConditions,
                        onNativePreparation: evidence.record, observeOutcome: true)
                    evidence.observe(outcome)
                } catch let error as DriverError where error.code == .inputUnavailable {
                    throw NativePreparationUnavailable(description: error.description)
                }
            case .systemPointer:
                try AuthorizeNativeClickForegroundPass.run(plan)
                try await PrepareNativeClickForegroundPass.run(
                    preparation, boundary: boundary, receipt: receipt, evidence: evidence)
            case .objectAction, .objectSelection: break
            }
        }
        try await NativeClickExposurePipeline.run(plan, boundary: boundary, receipt: receipt, evidence: evidence)
        try ConfirmNativeClickPreparationPass.run(boundary)
        if let current = try SampleNativeClickPreparationPass.run(plan, boundary: boundary, afterPreparation: true),
            current.needed
        {
            throw NativePreparationUnavailable(
                description: "selected click delivery remains unprepared; requested input was not sent")
        }
    }
}
