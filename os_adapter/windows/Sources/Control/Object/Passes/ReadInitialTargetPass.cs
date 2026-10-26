using System;
using System.Collections.Generic;
using System.Linq;

namespace ComputerUse.Windows
{
    // Input/output: NativeObjectState Run(ObjectDispatchPlan plan, Request r, INativeObjectProvider provider). Borrows only the named native capability.
    // Failures propagate unchanged; this stage never retries or selects a route.
    // State writes: only returned/local facts or the passed receipt; no ownership transfer.
    // Failure boundary: original native errors propagate to the owning scope/receipt.
    internal static class ReadInitialTargetPass
    {
        internal const string Effects = "QUERY";
        internal static NativeObjectState Run(ObjectDispatchPlan plan, Request r, INativeObjectProvider provider)
        {
            var state = provider.Read(plan.Target, plan.Contract.Within, r);
            NativeObjectChecks.CheckState(state, plan.Contract);
            return state;
        }
    }
}
