// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

using System;

namespace ComputerUse.Windows
{
    // Physical HWND geometry is not a new screenshot or pixel allocation.
    // Keep it independent of capture scaling and screenshot pixel budgets.
    internal sealed class NativeCapturedWindowFrame
    {
        internal readonly int X, Y, NativeWidth, NativeHeight;
        internal NativeCapturedWindowFrame(int x, int y, int width, int height)
        {
            X = x; Y = y; NativeWidth = width; NativeHeight = height;
        }
        internal static NativeCapturedWindowFrame From(NativeCaptureGeometry geometry)
        {
            return new NativeCapturedWindowFrame(geometry.X, geometry.Y, geometry.NativeWidth, geometry.NativeHeight);
        }
    }

    // One execution's immutable physical trajectory. The retained image and
    // its capture-time full-window frame are never rewritten or refreshed.
    internal sealed class NativeCapturedPointerFrame
    {
        internal readonly NativeCapturedWindowFrame WindowBounds;
        readonly NativePointerPoint[] points;
        internal int Count { get { return points.Length; } }
        internal NativePointerPoint Point(int index) { return points[index]; }

        internal NativeCapturedPointerFrame(NativePointerCapture capture, NativeCapturedWindowFrame currentWindow,
                                            NativePointerPoint[] originalPoints)
        {
            WindowBounds = capture.Binding["surface"] == null ? null : currentWindow;
            points = new NativePointerPoint[originalPoints.Length];
            for (int index = 0; index < points.Length; index++)
                points[index] = Project(capture, currentWindow, originalPoints[index]);
        }

        internal static void RequireWindow(NativePointerCapture capture, NativeCapturedWindowFrame frozenWindow,
                                           NativeCapturedWindowFrame currentWindow, bool ownedDrag)
        {
            if (ownedDrag)
                return; // Preserve the original absolute path under its actual mouse grab.
            var expected = frozenWindow ?? NativeCapturedWindowFrame.From(capture.WindowBounds);
            if (currentWindow == null || currentWindow.NativeWidth != expected.NativeWidth ||
                currentWindow.NativeHeight != expected.NativeHeight ||
                frozenWindow != null && (currentWindow.X != expected.X || currentWindow.Y != expected.Y))
                throw new Failure("snapshot_expired", "captured window frame changed; read and view a new image");
        }

        internal static NativePointerPoint Project(NativePointerCapture capture, NativeCapturedWindowFrame currentWindow,
                                                   NativePointerPoint original)
        {
            capture.RequireCapturedPoint(original);
            if (capture.Binding["surface"] == null)
                return original;
            RequireWindow(capture, null, currentWindow, false);
            long x = (long)original.X + currentWindow.X - capture.WindowBounds.X;
            long y = (long)original.Y + currentWindow.Y - capture.WindowBounds.Y;
            if (x < Int32.MinValue || x > Int32.MaxValue || y < Int32.MinValue || y > Int32.MaxValue)
                throw new Failure("snapshot_expired", "translated window point exceeds physical screen coordinates");
            var translated = new NativePointerPoint((int)x, (int)y);
            capture.RequirePoint(translated);
            return translated;
        }
    }
}
