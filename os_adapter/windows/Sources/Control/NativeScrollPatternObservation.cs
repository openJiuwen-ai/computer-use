// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

using System;

namespace ComputerUse.Windows
{
    // Only pattern properties used by this selected scroll recipe are read.
    // Target identity/scope/modal validation belongs to the native provider.
    internal static class NativeScrollPatternObservation
    {
        internal static void Read(NativeScrollState state, bool needsPosition, Func<bool> scrollable,
                                  Func<double?> percent, Func<double?> viewSize)
        {
            state.Scrollable = scrollable();
            if (needsPosition && state.Scrollable == true)
            {
                state.Percent = percent();
                state.ViewSize = viewSize();
            }
        }
    }
}
