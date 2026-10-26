using System;
using System.Collections.Generic;
using System.Linq;

namespace ComputerUse.Windows
{
    // Input/output: void Run(ObjectDispatchPlan plan, Request r, INativeObjectProvider provider). Borrows only the named native capability.
    // Failures propagate unchanged; this stage never retries or selects a route.
    // State writes: only returned/local facts or the passed receipt; no ownership transfer.
    // Failure boundary: original native errors propagate to the owning scope/receipt.
    internal static class CheckDispatchConditionsPass
    {
        internal const string Effects = "QUERY";
        internal static void Run(ObjectDispatchPlan plan, Request r, INativeObjectProvider provider)
        {
            // Pattern acquisition can run callbacks. Keep this second read
            // next to Sending/mutation inside the same native execute call.
            var current = provider.Read(plan.Target, plan.Contract.Within, r);
            NativeObjectChecks.CheckState(current, plan.Contract);
        }
    }
}
