// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

using System;
using System.Collections.Generic;
using System.Linq;

namespace ComputerUse.Windows
{
    // Input/output: void Run(Action mutate, Dictionary<string, object> result). Borrows only the named native capability.
    // Failures propagate unchanged; this stage never retries or selects a route.
    // State writes: requested native input and the passed dispatch receipt.
    // Failure boundary: original native errors propagate to the owning scope/receipt.
    internal static class SendNativeUnitPass
    {
        internal const string Effects = "DISPATCH";
        internal static void Run(Action mutate, Dictionary<string, object> result)
        {
            mutate();
            result["dispatch"] = "dispatched";
        }
    }
}
