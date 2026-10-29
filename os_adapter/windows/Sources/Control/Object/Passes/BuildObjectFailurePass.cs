// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

using System;
using System.Collections.Generic;
using System.Linq;

namespace ComputerUse.Windows
{
    // PURE: preserve the exact dispatch-sensitive error/retry receipt.
    // State writes: only returned/local facts or the passed receipt; no ownership transfer.
    // Failure boundary: original native errors propagate to the owning scope/receipt.
    internal static class BuildObjectFailurePass
    {
        internal const string Effects = "PURE";
        internal static void Run(Exception error, NativeWriteOperation operation, Dictionary<string, object> result)
        {
            var failure = error as Failure;
            result["errorCode"] = failure != null ? failure.Code : operation.Dispatched ? "execution_unknown" : "input_unavailable";
            result["detail"] = error.Message.Length == 0 ? "Native object operation failed." : error.Message;
            if (!operation.Dispatched && (string)result["errorCode"] == "input_unavailable")
                result["retryReason"] = "routeUnavailable";
        }
    }
}
