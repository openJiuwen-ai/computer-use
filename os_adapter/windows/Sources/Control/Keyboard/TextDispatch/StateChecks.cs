// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

using System;

namespace ComputerUse.Windows
{
    // PURE shared contract checks. Neither inspection nor continuous typing
    // can weaken enabled/read-only/selection requirements by using a new port.
    internal static class NativeKeyboardStateChecks
    {
        internal static void Validate(NativeInputContract contract, NativeKeyboardState state, bool preparing = false)
        {
            if (state.Role != contract.Role || state.Enabled != true)
                throw new Failure("input_unavailable", "the original keyboard target role or enabled state changed");
            if (contract.Text && contract.Target["node"] != null && state.TextWritable != true ||
                contract.Replacement && !state.TextSelection && !(preparing && state.SelectionPreparable))
                throw new Failure("input_unavailable",
                                  "the selected control no longer declares the required text capability");
        }
    }
}
