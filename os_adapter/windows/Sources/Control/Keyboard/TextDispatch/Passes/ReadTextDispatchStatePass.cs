// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

namespace ComputerUse.Windows
{
    internal static class ReadTextDispatchStatePass
    {
        internal const string Effects = "QUERY";
        internal static NativeKeyboardState Run(INativeTypedTextDispatchSequence sequence, NativeInputContract contract,
                                                Request request)
        {
            request.Check();
            var state = sequence.ReadDispatchState(contract, request);
            request.Check();
            return state;
        }
    }
}
