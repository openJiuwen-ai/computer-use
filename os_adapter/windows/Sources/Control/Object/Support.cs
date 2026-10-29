// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

using System;
using System.Collections.Generic;
using System.Linq;

namespace ComputerUse.Windows
{
    internal static class NativeObjectChecks
    {
        internal static void CheckState(NativeObjectState state, NativeInputContract contract)
        {
            if (state.Enabled != true || state.Role != contract.Role)
                throw new Failure("input_unavailable", "the selected control's enabled state or role changed");
            if (contract.Unit == "native_action" && (!state.ActionsComplete || state.Actions.Count(x => x == contract.Action) != 1) || contract.Unit == "native_text_edit" && state.Replaceable != true)
                throw new Failure("input_unavailable", "the selected native capability is no longer available");
        }
    }
}
