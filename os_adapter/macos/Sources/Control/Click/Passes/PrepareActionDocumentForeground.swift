// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit

/// PREPARE: obtain the authorized exact document without a content mouse primer.
@MainActor enum PrepareActionDocumentForegroundPass {
    static func run(
        _ unit: NativeDocumentContext, boundary: NativeUnitBoundary, receipt: NativeExecutionReceipt,
        evidence: NativeClickEvidence
    ) async throws {
        try boundary.checkConditions()
        let mapping = try NativeWindowUnit(application: unit.application, window: unit.window).mapping()
        var changed = false
        try await ForegroundAssist().prepare(
            BackgroundTarget(unit.application), window: mapping.id,
            onAction: {
                if !changed {
                    evidence.record(
                        .init(
                            kind: .foregroundActivation, phase: .willSend,
                            detail: "action document foreground preparation", window: mapping.id))
                }
                changed = true
                receipt.recordPreparation("action document foreground preparation")
            }, onWait: evidence.recordWait, onCleanupUnknown: receipt.recordCleanupUnknown)
        if changed {
            evidence.record(
                .init(
                    kind: .foregroundActivation, phase: .returned, detail: "action document foreground preparation",
                    window: mapping.id))
        }
        try boundary.checkConditions()
    }
}
