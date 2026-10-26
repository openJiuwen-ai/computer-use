using System;
using System.Collections.Generic;
using System.Linq;

namespace ComputerUse.Windows
{
    // QUERY: refresh the original target using the already selected guard.
    // State writes: only returned/local facts or the passed receipt; no ownership transfer.
    // Failure boundary: original native errors propagate to the owning scope/receipt.
    internal static class InspectPreparedTargetPass
    {
        internal const string Effects = "QUERY";
        internal static NativeKeyboardState Run(NativeInputContract contract, Request r, Func<NativeInputContract, Request, bool, NativeKeyboardState> guard)
        {
            return guard(contract, r, true);
        }
    }
}
