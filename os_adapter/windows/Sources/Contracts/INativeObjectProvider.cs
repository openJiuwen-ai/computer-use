// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

using System;
using System.Collections.Generic;
using System.Linq;

namespace ComputerUse.Windows
{
    internal interface INativeObjectProvider
    {
        IDisposable Acquire(Request request);
        NativeObjectState Read(Dictionary<string, object> target, string[] within, Request request);
        Action Prepare(NativeObjectState state, string unit, string action, string text, Request request);
    }

    // Optional v1 observation port. Semantic metadata may be unavailable while
    // the retained target still has an independently validated pointer route.
    // Implementations must revalidate identity/scope after a metadata failure.
    internal interface INativeClickObjectProvider
    {
        NativeObjectState ReadClick(Dictionary<string, object> target, string[] within, Request request);
    }
}
