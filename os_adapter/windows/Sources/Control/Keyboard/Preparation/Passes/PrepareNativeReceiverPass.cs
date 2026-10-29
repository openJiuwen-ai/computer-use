// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

using System;
using System.Collections.Generic;
using System.Linq;

namespace ComputerUse.Windows
{
    // Input/output: NativeKeyboardState Run(NativeInputContract contract, NativeKeyboardState state, Request r, List<string> preparation, INativeKeyboardProvider provider, Func<NativeInputContract, Request, bool, NativeKeyboardState> guard). Borrows only the named native capability.
    // Failures propagate unchanged; this stage never retries or selects a route.
    // State writes: the selected native preparation, request/preparation evidence and returned local records.
    // Failure boundary: original native errors propagate to the owning scope/receipt.
    internal static class PrepareNativeReceiverPass
    {
        internal const string Effects = "PREPARE/QUERY";
        internal static NativeKeyboardState Run(NativeInputContract contract, NativeKeyboardState state, Request r, List<string> preparation, INativeKeyboardProvider provider, Func<NativeInputContract, Request, bool, NativeKeyboardState> guard)
        {
            if (contract.Target["node"] != null && !NativeKeyboard.Ready(state, contract.Replacement) && state.Focusable == true)
            {
                provider.RequireIdle(new NativeKeyEvent[0], r);
                // Idle/provider callbacks may change focus and identity too.
                state = guard(contract, r, true);
                if (!state.Foreground)
                    return null;
                if (!NativeKeyboard.Ready(state, contract.Replacement) && state.Focusable == true)
                {
                    r.Check();
                    preparation.Add("nativeFocus");
                    r.Effect = true;
                    provider.Focus(state, r);
                }
            }

            return state;
        }
    }
}
