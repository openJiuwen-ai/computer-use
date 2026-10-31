// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

using System;

namespace ComputerUse.Windows
{
    internal static class NativeElementCapturePolicy
    {
        // A point hit and capture in disjoint native child branches prove that
        // the input will reach a sibling rather than the observed element.
        // Root/ancestor capture and related popup transports retain their
        // existing semantics; unknown geometry is not fabricated as conflict.
        internal static bool KnownSibling(IntPtr window, IntPtr recipient, IntPtr capture,
                                          Func<IntPtr, IntPtr, bool> inside)
        {
            return window != IntPtr.Zero && recipient != IntPtr.Zero && capture != IntPtr.Zero &&
                   inside(window, recipient) && inside(window, capture) && !inside(recipient, capture) &&
                   !inside(capture, recipient);
        }
    }
}
