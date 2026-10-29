// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices

/// Explicit preparation composition for the fixed selected delivery. No
/// attempt loop, cross-delivery selection or fresh request deadline lives here.
@MainActor enum NativeClickPreparationPipeline {
    static func run(
        _ plan: NativeClickPlan, boundary: NativeUnitBoundary, receipt: NativeExecutionReceipt,
        evidence: NativeClickEvidence
    ) async throws {
        let metric = RequestDiagnostics.metrics?.start(.clickPreparation)
        defer { metric?.end() }
        if let document = plan.document, plan.documentPreparationRequired {
            if !BackgroundTarget(document.application).isForeground {
                // AXMain/AXFocusedWindow can change while the native document
                // responder remains inactive. Do not dispatch a hollow press.
                guard plan.command.foreground else {
                    throw NativePreparationUnavailable(
                        description: """
                            background document selection cannot establish the native command receiver; \
                            AXPress was not sent
                            """)
                }
                try AuthorizeNativeClickForegroundPass.run(plan)
                try await PrepareActionDocumentForegroundPass.run(
                    document, boundary: boundary, receipt: receipt, evidence: evidence)
            }
            try PrepareActionDocumentContextPass.run(document, boundary: boundary, receipt: receipt, evidence: evidence)
            try await ConfirmActionDocumentContextPass.run(document, boundary: boundary, evidence: evidence)
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
            case .windowDirected:
                let pointer = preparation.pointer
                try pointer.requireWindowDirectedPreparation()
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
