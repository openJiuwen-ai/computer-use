// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

namespace ComputerUse.Windows
{
    internal static class CheckCharacterIdlePass
    {
        internal const string Effects = "QUERY";
        internal static void Run(INativeCharacterTextSequence sequence, Request request)
        {
            request.Check();
            sequence.RequireCharacterIdle(request);
            request.Check();
        }
    }
}
