// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

namespace ComputerUse.Windows
{
    // QUERY: a fresh UIA focused identity after retained-object callbacks.
    internal static class ValidateTextDispatchFocusPass
    {
        internal const string Effects = "QUERY";
        internal static void Run(INativeTypedTextDispatchSequence sequence, Request request)
        {
            request.Check();
            sequence.ValidateDispatchFocus(request);
            request.Check();
        }
    }
}
