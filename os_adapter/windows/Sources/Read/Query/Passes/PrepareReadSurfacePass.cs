using System;
using System.Collections.Generic;
using System.Linq;

namespace ComputerUse.Windows
{
    // Input/output: void Run(NativeQueryRequest scope, bool directory, Request r, NativeWriteOperation write, INativeReadBackend reader). Borrows only the named native capability.
    // Failures propagate unchanged; this stage never retries or selects a route.
    // State writes: the selected native preparation, request/preparation evidence and returned local records.
    // Failure boundary: original native errors propagate to the owning scope/receipt.
    internal static class PrepareReadSurfacePass
    {
        internal const string Effects = "PREPARE/QUERY";
        internal static void Run(NativeQueryRequest scope, bool directory, Request r, NativeWriteOperation write, INativeReadBackend reader)
        {
            // Existing platform preparation is PREPARE, not a fact query. Keep
            // its explicit place and policy; do not silently erase it in a
            // structural refactor. Capture retains its non-activating variant.
            var preparation = write == null && !directory && (string)scope.Query["kind"] != "readChange" ? reader as INativeReadPreparation : null;
            if (preparation != null && preparation.NeedsPreparation(scope.Target))
            {
                preparation.PrepareRead(scope.Target, r, true);
                reader.Validate(scope.Target, r);
            }
        }
    }
}
