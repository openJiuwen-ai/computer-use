// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

using System;
using System.Collections.Generic;
using System.Linq;

namespace ComputerUse.Windows
{
    // Input/output: bool Run(NativeQueryRequest scope, NativeOperationView state). Borrows only the named native capability.
    // Failures propagate unchanged; this stage never retries or selects a route.
    // State writes: only returned/local facts or the passed receipt; no ownership transfer.
    // Failure boundary: original native errors propagate to the owning scope/receipt.
    internal static class CheckQueryBoundaryPass
    {
        internal const string Effects = "PURE";
        internal static bool Run(NativeQueryRequest scope, NativeOperationView state)
        {
            var requested = scope.Target;
            var query = scope.Query;
            var token = scope.Token;
            if (state.Operation == null || state.Operation != token)
                throw new Failure("stale_target", "native operation is unknown or expired");
            if (NativeBinding.Keys.Any(key => key != "node" && !Equals(state.Original[key], requested[key])))
                throw new Failure("stale_target", "native observation cannot change its original scope");
            bool directory = new[]
            {
                "applications",
                "surfaces",
                "capturedText"
            }.Contains(Wire.Str(query, "kind"));
            if (requested["capture"] != null && (string)query["kind"] != "inputContext")
                throw new Failure("invalid_request", "captured image input has no accessibility node queries");
            bool desktopInput = state.Write != null && requested["surface"] == null && requested["capture"] != null && (string)query["kind"] == "inputContext";
            if (!desktopInput && ((requested["surface"] == null) != directory || !directory && requested["node"] == null && (string)query["kind"] != "inputContext"))
                throw new Failure("invalid_request", "native query requires its corresponding directory or node scope");
            return directory;
        }
    }
}
