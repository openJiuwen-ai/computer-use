using System;
using System.Collections.Generic;
using System.Linq;

namespace ComputerUse.Windows
{
    // QUERY/RESOURCE: inspect only the native input declaration capability.
    // State writes: original native resource/operation fields explicitly passed by reference.
    // Failure boundary: original native errors propagate to the owning scope/receipt.
    internal static class CollectInputContextPass
    {
        internal const string Effects = "QUERY/RESOURCE";
        internal static object Run(NativeQueryRequest scope, Request r, NativeWriteOperation write, Func<NativeWriteOperation, Dictionary<string, object>, string[], Request, object> inspectInput)
        {
            return inspectInput(write, scope.Target, Wire.Array(scope.Query["within"]).Cast<string>().ToArray(), r);
        }
    }
}
