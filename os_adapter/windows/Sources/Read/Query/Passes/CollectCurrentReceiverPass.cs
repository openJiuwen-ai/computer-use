using System;
using System.Collections.Generic;

namespace ComputerUse.Windows
{
    // QUERY/RESOURCE: discover only the focused object and its scope path.
    // No input contracts, preparation, mutations or fallback branch are run.
    // Optional focus failure is returned as evidence; scope failure propagates.
    internal static class CollectCurrentReceiverPass
    {
        internal const string Effects = "QUERY/RESOURCE";
        internal static object Run(NativeQueryRequest scope, Request r, NativeWriteOperation write, Func<NativeWriteOperation, Dictionary<string, object>, Request, object> inspectCurrentReceiver)
        {
            if (scope.Target["node"] == null || scope.Target["capture"] != null || Wire.Array(scope.Query["within"]).Length != 0)
                throw new Failure("invalid_request", "current receiver discovery requires an unselected element scope");
            if (inspectCurrentReceiver == null)
                throw new Failure("mode_unsupported", "native current receiver discovery is unavailable");
            return inspectCurrentReceiver(write, scope.Target, r);
        }
    }
}
