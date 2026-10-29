// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

using System;
using System.Collections.Generic;
using System.Linq;

namespace ComputerUse.Windows
{
    // The original Runtime retains lease/retired fields and the one step journal.
    internal static class NativeResourcePipeline
    {
        internal static void Acquire(bool writable, Dictionary<string, object> target, Request r, INativeReadBackend reader, Func<Request, IDisposable> acquire, ref IDisposable lease, ref bool retired)
        {
            AcquireNativeResourcesPass.Run(writable, target, r, reader, acquire, ref lease, ref retired);
        }

        internal static string Release(string cleanup, INativeReadBackend reader, ref IDisposable lease, ref bool retired)
        {
            return ReleaseNativeResourcesPass.Run(cleanup, reader, ref lease, ref retired);
        }
    }
}
