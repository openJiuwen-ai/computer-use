// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

namespace ComputerUse.Windows
{
    internal static class ValidateSelectionInsertFinalPass
    {
        internal const string Effects = "QUERY";
        internal static void Run(INativeSelectionInsertSequence sequence, NativeSelectionInsertState state,
                                 Request request)
        {
            request.Check();
            sequence.ValidateFinal(state, request);
            request.Check();
        }
    }
}
