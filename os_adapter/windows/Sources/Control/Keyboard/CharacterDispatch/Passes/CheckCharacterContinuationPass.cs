// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

namespace ComputerUse.Windows
{
    internal static class CheckCharacterContinuationPass
    {
        internal const string Effects = "QUERY";
        internal static void Run(INativeCharacterTextSequence sequence, Request request)
        {
            request.Check();
            sequence.ValidateCharacterContinuation(request);
            request.Check();
        }
    }
}
