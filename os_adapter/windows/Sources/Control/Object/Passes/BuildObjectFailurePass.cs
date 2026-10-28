// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

using System;
using System.Collections.Generic;
using System.Linq;

namespace ComputerUse.Windows
{
    // PURE/RESOURCE: preserve dispatch-sensitive error/retry facts and report
    // typed cleanup evidence to the existing operation health owner.
    // State writes: receipt and that owner only; no second retirement owner.
    // Failure boundary: original native errors propagate to the owning scope/receipt.
    internal static class BuildObjectFailurePass
    {
        internal const string Effects = "PURE/RESOURCE";
        internal static void Run(Exception error, NativeWriteOperation operation, Dictionary<string, object> result)
        {
            if (operation.AppInsertPin != null && operation.AppInsertPin.AppInsertRecipient != null)
                error = NativeAppInsertRecipientFailure.Preserve(error);
            var desktopCleanup = error as NativeDesktopCleanupFailure;
            if (desktopCleanup != null)
                operation.RetireWorker(desktopCleanup.Reason);
            var failure = NativeAppInsertRecipientFailure.FailureForClassification(
                error as Failure ?? (desktopCleanup == null ? null : desktopCleanup.OriginalFailure));
            result["errorCode"] = failure != null        ? failure.Code
                                  : operation.Dispatched ? "execution_unknown"
                                                         : "input_unavailable";
            result["detail"] = error.Message.Length == 0 ? "Native object operation failed." : error.Message;
            if (!(error is NativeAppInsertRecipientFailure) && !operation.Dispatched &&
                (string)result["errorCode"] == "input_unavailable")
                result["retryReason"] = "routeUnavailable";
        }
    }
}
