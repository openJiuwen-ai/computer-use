// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

using System;
using System.Collections.Generic;
using System.Linq;

namespace ComputerUse.Windows
{
    // QUERY: preserve replacement capability acquisition before readiness return.
    // State writes: only returned/local facts or the passed receipt; no ownership transfer.
    // Failure boundary: original native errors propagate to the owning scope/receipt.
    internal static class InspectReplacementPass
    {
        internal const string Effects = "QUERY";
        internal static void Run(NativeInputContract contract, NativeKeyboardState state, string text, Request r, INativeKeyboardTextProvider textProvider)
        {
            var replacements = textProvider as INativeKeyboardReplacementProvider;
            if (contract.Replacement && state.TextSelection && replacements != null)
                replacements.Replacement(state, text, r);
        }
    }
}
