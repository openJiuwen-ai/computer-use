// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

namespace ComputerUse.Windows
{
    internal static class CheckTextDispatchStatePass
    {
        internal const string Effects = "PURE";
        internal static void Run(NativeKeyboardState state, NativeInputContract contract, Request request)
        {
            request.Check();
            NativeKeyboardStateChecks.Validate(contract, state);
        }
    }
}
