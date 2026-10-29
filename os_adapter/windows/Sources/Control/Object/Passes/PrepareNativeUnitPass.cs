// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

using System;
using System.Collections.Generic;
using System.Linq;

namespace ComputerUse.Windows
{
    // Input/output: Action Run(ObjectDispatchPlan plan, NativeObjectState state, Request r, INativeObjectProvider provider). Borrows only the named native capability.
    // Failures propagate unchanged; this stage never retries or selects a route.
    // State writes: only returned/local facts or the passed receipt; no ownership transfer.
    // Failure boundary: original native errors propagate to the owning scope/receipt.
    internal static class PrepareNativeUnitPass
    {
        internal const string Effects = "QUERY";
        internal static Action Run(ObjectDispatchPlan plan, NativeObjectState state, Request r, INativeObjectProvider provider)
        {
            return provider.Prepare(state, plan.Unit, plan.Contract.Action, plan.Text, r);
        }
    }
}
