// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

using System;
using System.Collections.Generic;
using System.Linq;

namespace ComputerUse.Windows
{
    // QUERY: optional post-value observation; never substitutes another input.
    // State writes: only returned/local facts or the passed receipt; no ownership transfer.
    // Failure boundary: original native errors propagate to the owning scope/receipt.
    internal static class ObserveObjectEffectPass
    {
        internal const string Effects = "QUERY";
        internal static void Run(ObjectDispatchPlan plan, NativeObjectState state, Request r, Dictionary<string, object> result, INativeObjectProvider provider)
        {
            // The setter returned. Its original boundary still applies, while
            // enabled/writable/pattern state may have been consumed or changed
            // by that successful mutation. Observe without authorizing a new one.
            var after = NativeObjectChecks.ReadForContract(plan, r, provider);
            if (after.TextValueKnown && after.TextValue == plan.Text)
            {
                result["confirmation"] = "confirmed";
                result["detail"] = "Native replacement returned; the requested control value was observed. Submission still requires a separate action.";
            }
            else if (state.TextValueKnown && after.TextValueKnown && after.TextValue == state.TextValue)
            {
                result["detail"] = "Native replacement returned, but the control value remains unchanged and the requested literal text was not observed. No alternate input was sent; inspect the control or use its visible editing controls.";
            }
            else
            {
                result["detail"] = "Native replacement returned. Requested literal value: unconfirmed. Alternate input: not sent.";
            }
        }
    }
}
