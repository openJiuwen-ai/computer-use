using System;
using System.Collections.Generic;
using System.Linq;

namespace ComputerUse.Windows
{
    // Input/output: NativeKeyboardState Run(NativeInputContract contract, NativeKeyboardState state, Request r, Func<NativeInputContract, Request, bool, NativeKeyboardState> guard). Borrows only the named native capability.
    // Failures propagate unchanged; this stage never retries or selects a route.
    // State writes: only returned/local facts or the passed receipt; no ownership transfer.
    // Failure boundary: original native errors propagate to the owning scope/receipt.
    internal static class ObservePreparationPass
    {
        internal const string Effects = "QUERY";
        internal static NativeKeyboardState Run(NativeInputContract contract, NativeKeyboardState state, Request r, Func<NativeInputContract, Request, bool, NativeKeyboardState> guard)
        {
            double deadline = Math.Min(r.Deadline, Wire.Now + 400);
            while (true)
            {
                state = guard(contract, r, true);
                if (NativeKeyboard.Ready(state, contract.Replacement))
                    return state;
                if (Wire.Now >= deadline)
                    break;
                r.Wait(20);
            }

            return state;
        }
    }
}
