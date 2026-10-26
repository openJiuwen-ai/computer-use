using System;
using System.Collections.Generic;
using System.Linq;

namespace ComputerUse.Windows
{
    // Input/output: NativeQueryRequest Run(Dictionary<string, object> p, Func<object, Dictionary<string, object>> binding). Borrows only the named native capability.
    // Failures propagate unchanged; this stage never retries or selects a route.
    // State writes: only returned/local facts or the passed receipt; no ownership transfer.
    // Failure boundary: original native errors propagate to the owning scope/receipt.
    internal static class ResolveQueryScopePass
    {
        internal const string Effects = "PURE";
        internal static NativeQueryRequest Run(Dictionary<string, object> p, Func<object, Dictionary<string, object>> binding)
        {
            Wire.Keys(p, "operation,target,query");
            string token = Wire.Str(p, "operation");
            var requested = binding(p["target"]);
            var query = Wire.Object(p["query"]);
            NativeQuerySchema.Validate(query);
            return new NativeQueryRequest(token, requested, query);
        }
    }
}
