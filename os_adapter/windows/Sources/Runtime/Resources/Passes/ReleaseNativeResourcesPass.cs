using System;
using System.Collections.Generic;
using System.Linq;

namespace ComputerUse.Windows
{
    // Input/output: string Run(string cleanup, INativeReadBackend reader, ref IDisposable lease, ref bool retired). Borrows only the named native capability.
    // Failures propagate unchanged; this stage never retries or selects a route.
    // State writes: original native resource/operation fields explicitly passed by reference.
    // Failure boundary: original native errors propagate to the owning scope/receipt.
    internal static class ReleaseNativeResourcesPass
    {
        internal const string Effects = "RESOURCE";
        internal static string Run(string cleanup, INativeReadBackend reader, ref IDisposable lease, ref bool retired)
        {
            try
            {
                reader.Release();
            }
            catch
            {
                cleanup = "failed";
                retired = true;
            }

            if (lease != null)
            {
                try
                {
                    lease.Dispose();
                    lease = null;
                }
                catch
                {
                    cleanup = "failed";
                    retired = true;
                }
            }

            return cleanup;
        }
    }
}
