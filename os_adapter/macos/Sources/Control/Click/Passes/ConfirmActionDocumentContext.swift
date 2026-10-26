import AppKit

/// QUERY: require observed exact document context before the single AXPress.
@MainActor enum ConfirmActionDocumentContextPass {
    static func run(_ unit: NativeDocumentContext, boundary: NativeUnitBoundary, evidence: NativeClickEvidence)
        async throws
    {
        for attempt in 0..<12 {
            try boundary.checkConditions()
            if try unit.focus() == .target {
                evidence.recordWait(.satisfied)
                return
            }
            if attempt < 11 { try await Task.sleep(for: .milliseconds(25)) }
        }
        evidence.recordWait(.exhausted)
        throw NativePreparationUnavailable(
            description: "the exact document context remains unconfirmed; AXPress was not sent")
    }
}
