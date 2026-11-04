// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

using System;

namespace ComputerUse.Windows
{
    // Lifetime belongs to one native input provider/runtime, across operations.
    // Only our own accepted or possibly accepted down is remembered. The wait
    // never polls global last-input time or changes the user's mouse settings.
    internal sealed class NativePointerClickHistory
    {
        internal bool HasDown;
        object recipient;
        NativePointerPoint point;
        uint button;
        double submitted;
        internal void Record(object destination, NativePointerPoint at, uint down, double now)
        {
            HasDown = true;
            recipient = destination;
            point = at;
            button = down;
            submitted = now;
        }
        internal double Remaining(object destination, NativePointerPoint at, uint down, NativePointerClickTiming timing,
                                  double now)
        {
            if (!HasDown || recipient == null || destination == null || timing == null || button != down ||
                !Equals(recipient, destination))
                return 0;
            if (timing.Milliseconds < 1 || timing.Milliseconds > 5000 || timing.Width < 1 || timing.Height < 1)
                throw new Failure("input_unavailable",
                                  "the current double-click timing is unavailable; no requested input was sent");
            // Conservatively include the edge of the system double-click box.
            if (Math.Abs((long)at.X - point.X) * 2 > timing.Width || Math.Abs((long)at.Y - point.Y) * 2 > timing.Height)
                return 0;
            return Math.Max(0, submitted + timing.Milliseconds + 2 - now);
        }
    }
}
