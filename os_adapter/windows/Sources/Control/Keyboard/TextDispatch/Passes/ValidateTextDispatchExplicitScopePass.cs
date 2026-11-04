// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

namespace ComputerUse.Windows
{
    internal static class ValidateTextDispatchExplicitScopePass
    {
        internal const string Effects = "QUERY";
        internal static void Run(INativeTypedTextDispatchSequence sequence, Request request)
        {
            request.Check();
            sequence.ValidateDispatchExplicitScope(request);
            request.Check();
        }
    }
}
