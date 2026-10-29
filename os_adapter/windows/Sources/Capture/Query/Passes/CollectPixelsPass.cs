// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

using System;
using System.Collections.Generic;
using System.Linq;

namespace ComputerUse.Windows
{
    // QUERY/RESOURCE: the existing capture provider owns pixel handles and registration.
    // State writes: original native resource/operation fields explicitly passed by reference.
    // Failure boundary: original native errors propagate to the owning scope/receipt.
    internal static class CollectPixelsPass
    {
        internal const string Effects = "QUERY/RESOURCE";
        internal static object Run(NativeCaptureRequest value, Request r, INativeCaptureBackend capture)
        {
            var result = capture.Capture(value.Target, value.Kind, value.Dimension, r);
            r.Check();
            return result;
        }
    }
}
