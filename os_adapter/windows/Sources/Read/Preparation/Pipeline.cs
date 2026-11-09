// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

using System;
using System.Collections.Generic;

namespace ComputerUse.Windows
{
    // Explicit QUERY/PREPARE/RESOURCE scope, sharing the original read deadline
    // and the runtime's one native lease. Inspect and capture never enter it.
    internal static class NativeObservationPreparationPipeline
    {
        internal static object Run(Dictionary<string, object> p, Request r, NativeOperationView state,
                                   Func<object, Dictionary<string, object>> binding, INativeReadPreparation provider,
                                   Action<Dictionary<string, object>, Request> acquire)
        {
            Wire.Keys(p, "operation,target");
            var target = binding(p["target"]);
            if (state.Operation == null || Wire.Str(p, "operation") != state.Operation)
                throw new Failure("stale_target", "native operation is unknown or expired");
            if (state.Write != null || !NativeWriteOperation.Same(target, state.Original) ||
                target["surface"] == null || target["node"] != null || target["capture"] != null)
                throw new Failure("invalid_request", "observation preparation requires the original window read scope");
            if (provider == null)
                throw new Failure("unsupported_operation", "this native driver has no observation preparation provider");
            r.Deadline = Math.Min(r.Deadline, state.Deadline);
            r.Check();
            acquire(target, r);
            var receipt = provider.PrepareObservation(target, r);
            r.Check();
            return receipt;
        }
    }
}
