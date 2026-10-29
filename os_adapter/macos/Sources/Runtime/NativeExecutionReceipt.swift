// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import Foundation

/// Per-step evidence and wire formatting. The executor owns dispatch barriers,
/// live validation and retry authorization; recording a preparation is not input.
@MainActor final class NativeExecutionReceipt {
    let target: [String: Any]
    var dispatch = "notDispatched"
    var detail: String
    var confirmation: String
    var retryReason: String?
    var context: [String: Any]?
    var application: [String: Any]?
    private(set) var errorCode: FailureCode?
    private(set) var acceptedCount = 0
    private(set) var preparation = [String]()
    private(set) var cleanup = "complete"
    private var clipboard: String?
    private let onEffect: () -> Void
    var click: NativeClickEvidence?

    init(
        target: [String: Any], detail: String = "Native action dispatched; application effect unconfirmed.",
        confirmation: String = "unavailable", onEffect: @escaping () -> Void
    ) {
        self.target = target
        self.detail = detail
        self.confirmation = confirmation
        self.onEffect = onEffect
    }

    func recordPreparation(_ change: String) {
        preparation.append(change)
        onEffect()
    }
    func recordAccepted() { acceptedCount += 1 }
    func recordCleanupUnknown() { cleanup = "unknown" }

    func recordClipboardRestore(_ outcome: PasteboardTransaction.RestoreResult) {
        switch outcome {
        case .restored: clipboard = "Original clipboard contents restored."
        case .ownershipLost: clipboard = "A later or unconfirmed clipboard owner was preserved."
        case .failed:
            clipboard = "Clipboard restoration failed."
            if cleanup == "complete" { cleanup = "failed" }
        }
    }

    func recordFailure(_ error: Error) {
        // The request/operation timer cancels the Swift task. Preserve that
        // known deadline cause instead of exposing an opaque CancellationError.
        // An earlier caller cancellation has no expired deadline and stays one.
        let expired =
            error is CancellationError
            && RequestDiagnostics.deadline.map { ProcessInfo.processInfo.systemUptime >= $0 } == true
        let cause: Error = expired ? RequestDeadlineExceeded() : error
        errorCode = (cause as? ClassifiedFailure)?.code ?? .executionFailed
        detail = String(describing: cause)
        confirmation = "notRequested"
        if acceptedCount > 0 { dispatch = "partial" }
    }

    var succeeded: Bool { errorCode == nil && cleanup == "complete" }
    var wire: [String: Any] {
        var value: [String: Any] = [
            "target": target, "dispatch": dispatch, "detail": clipboard.map { "\(detail) \($0)" } ?? detail,
            "preparation": preparation, "confirmation": confirmation, "cleanup": cleanup,
            "errorCode": errorCode?.rawValue as Any? ?? NSNull(),
            "retryReason": (cleanup == "complete" ? retryReason : nil) as Any? ?? NSNull(),
            "context": context as Any? ?? NSNull()
        ]
        if let application { value["application"] = application }
        if let click { value["click"] = click.wire }
        return value
    }
}
