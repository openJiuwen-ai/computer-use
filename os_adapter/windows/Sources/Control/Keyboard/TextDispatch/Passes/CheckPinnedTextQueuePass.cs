// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

namespace ComputerUse.Windows
{
    internal static class CheckPinnedTextQueuePass
    {
        internal const string Effects = "QUERY";
        internal static void Run(INativeTypedTextDispatchSequence sequence, Request request)
        {
            request.Check();
            sequence.CheckNativeRecipient(request);
            request.Check();
        }
    }
}
