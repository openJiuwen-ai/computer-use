// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

namespace ComputerUse.Windows
{
    internal static class FreezeCapturedPointerFramePass
    {
        internal const string Effects = "QUERY/RESOURCE";
        // Preparation and cadence have completed. Freeze the capture's one
        // physical trajectory before final validation and the first event.
        internal static void Run(NativeInputContract contract, NativePointer pointer, Request request)
        {
            pointer.FreezeCapturedFrame(contract, request);
        }
    }
}
