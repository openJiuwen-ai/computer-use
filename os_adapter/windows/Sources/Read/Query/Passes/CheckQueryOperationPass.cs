// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

using System;
using System.Collections.Generic;
using System.Linq;

namespace ComputerUse.Windows
{
    // Input/output: void Run(NativeQueryRequest scope, NativeWriteOperation write, bool hasWriter). Borrows only the named native capability.
    // Failures propagate unchanged; this stage never retries or selects a route.
    // State writes: only returned/local facts or the passed receipt; no ownership transfer.
    // Failure boundary: original native errors propagate to the owning scope/receipt.
    internal static class CheckQueryOperationPass
    {
        internal const string Effects = "PURE";
        internal static void Run(NativeQueryRequest scope, NativeWriteOperation write, bool hasWriter)
        {
            if ((string)scope.Query["kind"] == "inputContext")
            {
                if (write == null || !hasWriter)
                    throw new Failure("invalid_request", "input contracts require a write operation");
            }
            else if ((string)scope.Query["kind"] == "readChange" && write != null)
                throw new Failure("invalid_request", "event-assisted waits require a read operation");
        }
    }
}
