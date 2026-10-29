// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

using System;
using System.Collections.Generic;
using System.Linq;

namespace ComputerUse.Windows
{
    // QUERY: existing live provider boundary validation, before operation checks.
    // State writes: only returned/local facts or the passed receipt; no ownership transfer.
    // Failure boundary: original native errors propagate to the owning scope/receipt.
    internal static class ValidateQueryTargetPass
    {
        internal const string Effects = "QUERY";
        internal static void Run(NativeQueryRequest scope, Request r, INativeReadBackend reader)
        {
            r.Check();
            reader.Validate(scope.Target, r);
        }
    }
}
