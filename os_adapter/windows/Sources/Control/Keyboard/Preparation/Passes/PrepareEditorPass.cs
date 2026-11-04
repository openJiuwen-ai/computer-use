// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

using System;
using System.Collections.Generic;
using System.Linq;

namespace ComputerUse.Windows
{
    // Input/output: bool Run(NativeInputContract contract, NativeKeyboardState state, NativeWriteOperation operation, Request r, List<string> preparation, INativeKeyboardProvider provider, Func<NativeInputContract, Request, bool, NativeKeyboardState> guard). Borrows only the named native capability.
    // Failures propagate unchanged; this stage never retries or selects a route.
    // State writes: the selected native preparation, request/preparation evidence and returned local records.
    // Failure boundary: original native errors propagate to the owning scope/receipt.
    internal static class PrepareEditorPass
    {
        internal const string Effects = "PREPARE/QUERY";
        internal static bool Run(NativeInputContract contract, NativeKeyboardState state, NativeWriteOperation operation, Request r, List<string> preparation, INativeKeyboardProvider provider, Func<NativeInputContract, Request, bool, NativeKeyboardState> guard)
        {
            var editor = provider as INativeKeyboardEditorPreparation;
            // A replacement explicitly selects the whole editor; insertion and
            // paste must never move an unknown caret by implicitly clicking.
            if (editor == null || !state.Foreground || !state.SelectionPreparable || !(contract.Replacement || (string)operation.Intent["kind"] == "focus"))
                return false;
            editor.PrepareEditor(state, operation, r, preparation);
            double deadline = Math.Min(r.Deadline, Wire.Now + 400);
            do
            {
                try
                {
                    state = guard(contract, r, true);
                }
                catch (Failure error)
                {
                    if (error.Code == "input_unavailable")
                        throw new Failure("execution_unknown", "The editor preparation click was sent, but the original receiver could not be confirmed. No text was sent; inspect the control before retrying. " + error.Message);
                    throw;
                }

                if (NativeKeyboard.Ready(state, contract.Replacement))
                    return true;
                if (Wire.Now >= deadline)
                    break;
                r.Wait(20);
            }
            while (true);
            throw new Failure("execution_unknown", "The focus preparation click was sent, but the original control's internal editor was not confirmed; no text was sent and the click was not replayed.");
        }
    }
}
