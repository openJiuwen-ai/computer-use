using System;
using System.Collections.Generic;
using System.Linq;

namespace ComputerUse.Windows
{
    // PREPARE/QUERY: preserve the existing non-activating observation preparation.
    // State writes: the selected native preparation, request/preparation evidence and returned local records.
    // Failure boundary: original native errors propagate to the owning scope/receipt.
    internal static class PrepareCaptureSurfacePass
    {
        internal const string Effects = "PREPARE/QUERY";
        internal static void Run(NativeCaptureRequest value, Request r, INativeReadBackend reader)
        {
            var preparation = reader as INativeReadPreparation;
            if (preparation != null && preparation.NeedsPreparation(value.Target))
            {
                preparation.PrepareRead(value.Target, r, false);
                reader.Validate(value.Target, r);
            }
        }
    }
}
