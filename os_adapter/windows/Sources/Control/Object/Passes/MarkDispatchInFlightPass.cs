// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

using System;
using System.Collections.Generic;
using System.Linq;

namespace ComputerUse.Windows
{
    // Input/output: void Run(NativeWriteOperation operation, Request r, Dictionary<string, object> result). Borrows only the named native capability.
    // Failures propagate unchanged; this stage never retries or selects a route.
    // State writes: original native resource/operation fields explicitly passed by reference.
    // Failure boundary: original native errors propagate to the owning scope/receipt.
    internal static class MarkDispatchInFlightPass
    {
        internal const string Effects = "RESOURCE";
        internal static void Run(NativeWriteOperation operation, Request r, Dictionary<string, object> result)
        {
            if (operation.ClickV1)
                operation.Click.BeginCall(r, "UIA", 0);
            else
            {
                r.Sending();
                operation.Dispatched = true;
            }

            result["dispatch"] = "unknown";
        }
    }
}
