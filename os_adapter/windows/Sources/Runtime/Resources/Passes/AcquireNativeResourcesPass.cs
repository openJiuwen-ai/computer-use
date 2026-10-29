// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

using System;
using System.Collections.Generic;
using System.Linq;

namespace ComputerUse.Windows
{
    // Input/output: void Run(bool writable, Dictionary<string, object> target, Request r, INativeReadBackend reader, Func<Request, IDisposable> acquire, ref IDisposable lease, ref bool retired). Borrows only the named native capability.
    // Failures propagate unchanged; this stage never retries or selects a route.
    // State writes: original native resource/operation fields explicitly passed by reference.
    // Failure boundary: original native errors propagate to the owning scope/receipt.
    internal static class AcquireNativeResourcesPass
    {
        internal const string Effects = "RESOURCE/QUERY";
        internal static void Run(bool writable, Dictionary<string, object> target, Request r, INativeReadBackend reader, Func<Request, IDisposable> acquire, ref IDisposable lease, ref bool retired)
        {
            try
            {
                var preparation = !writable ? reader as INativeReadPreparation : null;
                bool preparing = preparation != null && preparation.NeedsPreparation(target);
                if (writable || preparing && acquire != null)
                    lease = acquire(r);
                reader.Validate(target, r);
                r.Check();
            }
            catch
            {
                try
                {
                    reader.Release();
                }
                catch
                {
                    retired = true;
                }
                finally
                {
                    if (lease != null)
                    {
                        try
                        {
                            lease.Dispose();
                            lease = null;
                        }
                        catch
                        {
                            retired = true;
                        }
                    }
                }

                throw;
            }
        }
    }
}
