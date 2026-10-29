// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

using System;
using System.Collections.Generic;
using System.Linq;

namespace ComputerUse.Windows
{
    // Input/output: object Run(NativeQueryRequest scope, Request r, INativeReadBackend reader). Borrows only the named native capability.
    // Failures propagate unchanged; this stage never retries or selects a route.
    // State writes: original native resource/operation fields explicitly passed by reference.
    // Failure boundary: original native errors propagate to the owning scope/receipt.
    internal static class CollectRequestedFactsPass
    {
        internal const string Effects = "QUERY/RESOURCE";
        internal static object Run(NativeQueryRequest scope, Request r, INativeReadBackend reader)
        {
            // The UIA reader registers each retained object as it is collected;
            // encoding does not introduce a second reference owner or pass.
            return reader.Inspect(scope.Target, scope.Query, r);
        }
    }
}
