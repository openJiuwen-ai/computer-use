// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

using System;
using System.Collections.Generic;
using System.Linq;

namespace ComputerUse.Windows
{
    // Input/output: NativeKeyboardState Run(NativeInputContract contract, bool foreground, NativeKeyboardState state, Request r, List<string> preparation, INativeKeyboardProvider provider, Func<NativeInputContract, Request, bool, NativeKeyboardState> guard). Borrows only the named native capability.
    // Failures propagate unchanged; this stage never retries or selects a route.
    // State writes: the selected native preparation, request/preparation evidence and returned local records.
    // Failure boundary: original native errors propagate to the owning scope/receipt.
    internal static class PrepareForegroundPass
    {
        internal const string Effects = "PREPARE/QUERY";
        internal static NativeKeyboardState Run(NativeInputContract contract, bool foreground, NativeKeyboardState state, Request r, List<string> preparation, INativeKeyboardProvider provider, Func<NativeInputContract, Request, bool, NativeKeyboardState> guard)
        {
            if (!state.Foreground)
            {
                if (!foreground)
                    return null;
                provider.RequireIdle(new NativeKeyEvent[0], r);
                r.Check();
                preparation.Add("foreground");
                r.Effect = true;
                provider.Activate(state, r);
                var until = Math.Min(r.Deadline, Wire.Now + 400);
                while (!(state = guard(contract, r, true)).Foreground && Wire.Now < until)
                    r.Wait(20);
                if (!state.Foreground)
                    return null;
            }

            return state;
        }
    }
}
