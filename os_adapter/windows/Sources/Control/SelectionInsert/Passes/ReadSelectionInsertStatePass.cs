// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

namespace ComputerUse.Windows
{
    internal static class ReadSelectionInsertStatePass
    {
        internal const string Effects = "QUERY";
        internal static NativeSelectionInsertState Run(INativeSelectionInsertSequence sequence, Request request)
        {
            request.Check();
            var state = sequence.Read(request);
            request.Check();
            return state;
        }
    }
}
