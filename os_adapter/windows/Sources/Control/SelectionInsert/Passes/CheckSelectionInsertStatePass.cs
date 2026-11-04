// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

namespace ComputerUse.Windows
{
    internal static class CheckSelectionInsertStatePass
    {
        internal const string Effects = "PURE";
        internal static void Run(NativeSelectionInsertState state, string role)
        {
            if (state == null || state.Role != role || role != "ControlType.Edit" || state.Enabled != true ||
                state.Writable != true || !state.Eligible || !state.Ready || state.Selection == null ||
                state.Selection.Start < 0 || state.Selection.End < state.Selection.Start)
                throw new Failure(
                    "input_unavailable",
                    "the original native Edit is not a confirmed writable, ready selection-insertion target");
        }
    }
}
