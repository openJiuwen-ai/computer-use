// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import Foundation

/// RESOURCE: preserve effect evidence and issue bounded continuation facts; Python alone selects the next attempt.
@MainActor enum BuildNativeClickFailurePass {
    static func run(
        _ error: Error, command: NativeUnitCommand, operation: NativeOperation, step: Int64,
        offer: NativeClickContract?, within: [String], receipt: NativeExecutionReceipt, evidence: NativeClickEvidence
    ) {
        receipt.recordFailure(error)
        guard receipt.dispatch == "notDispatched", receipt.cleanup == "complete", evidence.permitsReselection else {
            receipt.retryReason = nil
            if !evidence.permitsReselection || receipt.cleanup != "complete" { operation.blockClickContinuation() }
            evidence.fail(dispatch: receipt.dispatch, cleanup: receipt.cleanup, retryReason: nil)
            return
        }
        var refreshWithinScope = false
        if let press = offer?.coordinateMenuPress,
            (error as? ClassifiedFailure)?.code == .inputUnavailable
        {
            // Only this optional plan is retired, only within this operation.
            // Re-enter the unchanged coordinate branch before any input; the
            // normal Gate still owns the single bounded refresh allowance.
            press.attempt.rejectedBeforeDispatch = true
            receipt.retryReason = "preconditionChanged"
            refreshWithinScope = true
        } else if error is NativeSelectionBasis.Changed {
            receipt.retryReason = "preconditionChanged"
        } else if error is NativePreparationUnavailable {
            receipt.retryReason = "preparationUnavailable"
            if let offer, !command.foreground, offer.preparationOptions.contains("background") {
                do {
                    try operation.recordClickBackgroundUnavailable(
                        family: offer.routeFamily, target: command.target, within: within, step: step)
                } catch { receipt.retryReason = nil }
            }
        } else if (error as? ClassifiedFailure)?.code == .inputUnavailable, !(error is NativeReceiverContinuity.Lost) {
            receipt.retryReason =
                offer.map { [.objectAction, .objectSelection].contains($0.delivery) } == true
                ? "routeUnavailable" : "preconditionChanged"
        }
        evidence.fail(dispatch: receipt.dispatch, cleanup: receipt.cleanup, retryReason: receipt.retryReason,
                      refreshWithinScope: refreshWithinScope)
    }
}
