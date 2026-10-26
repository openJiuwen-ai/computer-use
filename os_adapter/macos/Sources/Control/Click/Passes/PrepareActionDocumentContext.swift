import AppKit

/// PREPARE: select only the retained document; no editor focus or content input.
@MainActor enum PrepareActionDocumentContextPass {
    static func run(
        _ unit: NativeDocumentContext, boundary: NativeUnitBoundary, receipt: NativeExecutionReceipt,
        evidence: NativeClickEvidence
    ) throws {
        try unit.select(
            check: boundary.checkConditions, onPreparation: receipt.recordPreparation,
            onNativePreparation: evidence.record)
    }
}
