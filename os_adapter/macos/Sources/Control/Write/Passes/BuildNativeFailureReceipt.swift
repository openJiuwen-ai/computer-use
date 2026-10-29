// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices

/// RESOURCE: preserve failure/effect evidence and authorize only the original non-dispatched retry reasons.
@MainActor enum BuildNativeFailureReceiptPass {
    static func run(
        _ error: Error, kind: String, foreground: Bool, id: String, op: NativeOperation, receipt: NativeExecutionReceipt
    ) {
        receipt.recordFailure(error)
        if receipt.dispatch == "notDispatched", error is NativeSelectionBasis.Changed {
            receipt.retryReason = "preconditionChanged"
        } else if receipt.dispatch == "notDispatched", (error as? ClassifiedFailure)?.code == .inputUnavailable,
            !(error is NativeReceiverContinuity.Lost)
        {
            // A lost bound App receiver cannot be repaired by
            // re-inspecting and selecting another current control.
            if kind == "prepare_receiver", error is NativePreparationUnavailable {
                if foreground {
                    // Another declared preparation route may still
                    // work. No requested input has been dispatched.
                    receipt.retryReason = "preparationUnavailable"
                } else {
                    do {
                        try op.backgroundPreparationUnavailable(id)
                        receipt.retryReason = "preparationUnavailable"
                    } catch { /* Expired/cancelled operations cannot authorize activation. */  }
                }
            } else if kind != "prepare_receiver" {
                receipt.retryReason =
                    ["key_sequence", "clipboard_paste", "pointer_sequence"].contains(kind)
                    ? "preconditionChanged" : "routeUnavailable"
            }
        }

    }
}
