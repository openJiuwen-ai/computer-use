// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

namespace ComputerUse.Windows
{
    internal static class PrepareClickCadencePass
    {
        internal const string Effects = "PREPARE";
        internal static void Run(NativeInputContract contract, NativePointer pointer, Request r)
        {
            pointer.PrepareClickCadence(contract, r);
        }
    }
}
