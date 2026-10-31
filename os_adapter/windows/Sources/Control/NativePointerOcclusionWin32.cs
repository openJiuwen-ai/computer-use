// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

using System;

namespace ComputerUse.Windows
{
    // QUERY only. No band, activation, focus or input mutation port.
    internal sealed partial class NativeWindowsPointer
    {
        static bool OcclusionOrdinary(IntPtr handle)
        {
            int cloaked;
            return Native.IsWindow(handle) && Native.GetAncestor(handle, 2) == handle &&
                   Native.IsWindowVisible(handle) && !Native.IsIconic(handle) && Native.IsWindowEnabled(handle) &&
                   Native.DwmGetWindowAttribute(handle, 14, out cloaked, 4) == 0 && cloaked == 0;
        }
        NativeClickOcclusion ReadOcclusion(Window original, NativePointerPoint point, bool queueKnown, Native.GUI queue,
                                           bool physicalReady, Request r)
        {
            if (physicalReady || !NativePointerClickAdmission.QueueIdle(queueKnown, queue.Capture, queue.Flags) ||
                !Native.GenerationTrackingAvailable)
                return null;
            IntPtr target = original.Handle;
            Native.RECT rectangle;
            if (!OcclusionOrdinary(target) || !Native.GetWindowRect(target, out rectangle) ||
                rectangle.Right <= rectangle.Left || rectangle.Bottom <= rectangle.Top ||
                !rectangle.Box.Contains(point.X, point.Y))
                return null;
            IntPtr hit = Native.WindowFromPoint(new Native.POINT { X = point.X, Y = point.Y });
            if (hit == IntPtr.Zero || Native.Inside(target, hit))
                return null;
            IntPtr root = Native.GetAncestor(hit, 2);
            if (root == IntPtr.Zero || root == target || !OcclusionOrdinary(root))
                return null;
            r.Check();
            return new NativeClickOcclusion(
                root,
                new NativeClickTargetCondition(
                    request =>
                    {
                        request.Check();
                        if (!Native.GenerationTrackingAvailable)
                            throw new Failure("observation_incomplete",
                                              "native target lifetime tracking is unavailable");
                        original.Validate();
                        if (!OcclusionOrdinary(target))
                            throw new Failure("input_unavailable",
                                              "the original semantic click window is no longer visible and enabled");
                        request.Check();
                    }));
        }
    }
}
