// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

using System;
using System.Collections.Generic;
using System.Linq;

namespace ComputerUse.Windows
{
    // PURE: original lease/binding facts; no provider query or acquisition.
    // State writes: only returned/local facts or the passed receipt; no ownership transfer.
    // Failure boundary: original native errors propagate to the owning scope/receipt.
    internal static class ResolveCaptureScopePass
    {
        internal const string Effects = "PURE";
        internal static NativeCaptureRequest Run(Dictionary<string, object> p, Func<object, Dictionary<string, object>> binding, NativeOperationView state)
        {
            Wire.Keys(p, "operation,target,kind,maxDimension");
            string id = Wire.Str(p, "operation");
            var target = binding(p["target"]);
            string kind = Wire.Str(p, "kind");
            int dimension = (int)Wire.Num(p, "maxDimension", 1, 4096, true);
            if (state.Operation == null || id != state.Operation)
                throw new Failure("stale_target", "native operation is unknown or expired");
            if (state.Write != null || !NativeWriteOperation.Same(target, state.Original) || target["node"] != null || target["capture"] != null || !new[]
            {
                "window",
                "desktop"
            }.Contains(kind) || (target["surface"] != null) != (kind == "window"))
                throw new Failure("invalid_request", "capture requires the original window or desktop read scope");
            return new NativeCaptureRequest(target, kind, dimension);
        }
    }
}
