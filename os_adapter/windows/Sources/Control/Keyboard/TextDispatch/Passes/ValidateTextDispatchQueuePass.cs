// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

namespace ComputerUse.Windows
{
    internal static class ValidateTextDispatchQueuePass
    {
        internal const string Effects = "QUERY";
        internal static void Run(INativeTypedTextDispatchSequence sequence, NativeKeyboardState state, Request request)
        {
            request.Check();
            sequence.ValidateDispatchQueue(state, request);
            request.Check();
        }
    }
}
