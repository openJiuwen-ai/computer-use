// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

using System;
using System.Runtime.InteropServices;
using System.Text;

namespace ComputerUse.Windows
{
    internal static class NativeCharacterWin32
    {
        [DllImport("user32.dll", EntryPoint = "RealGetWindowClassW", ExactSpelling = true, CharSet = CharSet.Unicode,
                   SetLastError = true)]
        static extern uint RealGetWindowClassW(IntPtr window, StringBuilder name, uint count);
        [DllImport("user32.dll")]
        static extern bool IsWindowUnicode(IntPtr window);
        [DllImport("user32.dll", EntryPoint = "GetWindowLongPtrW", ExactSpelling = true, SetLastError = true)]
        static extern IntPtr WindowStyle(IntPtr window, int index);
        [DllImport("kernel32.dll")]
        static extern void SetLastError(uint error);
        [DllImport("user32.dll", EntryPoint = "SendMessageTimeoutW", ExactSpelling = true, SetLastError = true)]
        static extern IntPtr SendMessageTimeout(IntPtr window, uint message, IntPtr wParam, IntPtr lParam, uint flags,
                                                uint timeout, out UIntPtr result);
        internal static NativeCharacterClassEvidence ClassEvidence(IntPtr window)
        {
            var real = new StringBuilder(256);
            return new NativeCharacterClassEvidence {
                NativeClass = Native.Class(window),
                RealClass = RealGetWindowClassW(window, real, (uint)real.Capacity) > 0 ? real.ToString() : null,
                Unicode = IsWindowUnicode(window)
            };
        }
        internal static void RequireWritable(IntPtr window, IntPtr root, NativeCharacterClassFamily family)
        {
            if (!Native.IsWindow(window) || !Native.IsWindowEnabled(window) || !Native.IsWindowEnabled(root))
                throw new Failure(
                    "input_unavailable",
                    "the directed character recipient is no longer an enabled Unicode Edit-family window");
            NativeCharacterClasses.RequireNative(family, ClassEvidence(window));
            SetLastError(0);
            long style = WindowStyle(window, -16).ToInt64();
            int error = Marshal.GetLastWin32Error();
            if (style == 0 && error != 0 || (style & 0x800) != 0)
                throw new Failure("input_unavailable",
                                  "the original native Edit is read-only or its style cannot be verified");
        }
        sealed class Sender : INativeCharacterMessagePort
        {
            public NativeCharacterMessageResult Send(IntPtr window, char unit, int timeoutMs)
            {
                return NativeCharacterWin32.Send(window, unit, timeoutMs);
            }
        }
        internal static readonly INativeCharacterMessagePort Messages = new Sender();
        static NativeCharacterMessageResult Send(IntPtr window, char unit, int timeoutMs)
        {
            UIntPtr ignored;
            // SMTO_ABORTIFHUNG | SMTO_ERRORONEXIT. No NOTIMEOUTIFNOTHUNG:
            // the caller's original deadline remains authoritative.
            SetLastError(0);
            IntPtr sent = SendMessageTimeout(window, 0x102, new IntPtr(unit), new IntPtr(1), 2 | 32, (uint)timeoutMs,
                                             out ignored);
            int error = Marshal.GetLastWin32Error();
            Native.NoteVisualInput();
            return new NativeCharacterMessageResult(
                sent != IntPtr.Zero,
                sent == IntPtr.Zero ? "WM_CHAR native timeout/failure (code " + error + ")" : null);
        }
    }
}
