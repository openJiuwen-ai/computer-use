// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

namespace ComputerUse.Windows
{
    internal static class SendClickPointerPass
    {
        internal const string Effects = "DISPATCH";
        // Exactly one requested gesture with paired release. No route retries.
        internal static void Run(NativeWriteOperation operation, NativeInputContract contract, NativePointer pointer, Request r)
        {
            pointer.SendClick(operation, contract, r);
        }
    }
}
