import AppKit

/// PREPARE: obtain only the already-authorized exact foreground window.
@MainActor enum PrepareNativeClickForegroundPass {
    static func run(
        _ preparation: NativeClickWindowPreparation, boundary: NativeUnitBoundary, receipt: NativeExecutionReceipt,
        evidence: NativeClickEvidence
    ) async throws {
        let pointer = preparation.pointer
        try boundary.checkConditions()
        var changed = false
        try await ForegroundAssist().prepare(
            BackgroundTarget(pointer.application), window: pointer.window.id,
            pointerAt: pointer.foregroundPreparationPoint(at: preparation.point),
            onAction: {
                if !changed {
                    evidence.record(
                        .init(
                            kind: .foregroundActivation, phase: .willSend, detail: "foreground window activation",
                            window: pointer.window.id))
                }
                changed = true
                receipt.recordPreparation("foreground window activation")
            }, onWait: evidence.recordWait, onCleanupUnknown: receipt.recordCleanupUnknown)
        if changed {
            evidence.record(
                .init(
                    kind: .foregroundActivation, phase: .returned, detail: "foreground window activation",
                    window: pointer.window.id))
        }
        try boundary.checkConditions()
    }
}
