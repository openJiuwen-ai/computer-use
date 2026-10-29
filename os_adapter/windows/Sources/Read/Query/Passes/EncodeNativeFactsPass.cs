// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

using System;
using System.Collections.Generic;
using System.Linq;

namespace ComputerUse.Windows
{
    // PURE: return the existing facts after the original budget check.
    // State writes: only returned/local facts or the passed receipt; no ownership transfer.
    // Failure boundary: original native errors propagate to the owning scope/receipt.
    internal static class EncodeNativeFactsPass
    {
        internal const string Effects = "PURE";
        internal static object Run(object result, Request r)
        {
            r.Check();
            return result;
        }
    }
}
