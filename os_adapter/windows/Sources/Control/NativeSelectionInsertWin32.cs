// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

using System;
using System.Runtime.InteropServices;

namespace ComputerUse.Windows
{
    internal static class NativeSelectionInsertWin32
    {
        [DllImport("kernel32.dll")]
        static extern void SetLastError(uint error);
        [DllImport("user32.dll", EntryPoint = "GetWindowLongPtrW", ExactSpelling = true, SetLastError = true)]
        static extern IntPtr WindowStyle(IntPtr window, int index);
        [DllImport("user32.dll", EntryPoint = "SendMessageTimeoutW", ExactSpelling = true, SetLastError = true)]
        static extern IntPtr ReadSelection(IntPtr window, uint message, out uint start, out uint end, uint flags,
                                           uint timeout, out UIntPtr result);
        [DllImport("user32.dll", EntryPoint = "SendMessageTimeoutW", ExactSpelling = true, SetLastError = true)]
        static extern IntPtr SendMessageTimeout(IntPtr window, uint message, IntPtr wParam, IntPtr lParam, uint flags,
                                                uint timeout, out UIntPtr result);
        internal static long Style(IntPtr window)
        {
            SetLastError(0);
            long style = WindowStyle(window, -16).ToInt64();
            int error = Marshal.GetLastWin32Error();
            if (style == 0 && error != 0)
                throw new Failure("input_unavailable", "the original native edit style is unknown");
            return style;
        }
        internal static NativeSelectionInsertSelection Selection(IntPtr window, Request request)
        {
            request.Check();
            uint start, end;
            UIntPtr ignored;
            SetLastError(0);
            uint timeout = (uint)Math.Max(1, Math.Min(100, request.Deadline - Wire.Now));
            IntPtr completed = ReadSelection(window, 0xB0, out start, out end, 2 | 32, timeout, out ignored);
            int error = Marshal.GetLastWin32Error();
            request.Check();
            if (completed == IntPtr.Zero || start > Int32.MaxValue || end > Int32.MaxValue || end < start)
                throw new Failure("input_unavailable", "the original edit selection could not be read (code " + error +
                                                           "); no selection edit sent");
            return new NativeSelectionInsertSelection { Start = (int)start, End = (int)end };
        }
        sealed class Buffer : IDisposable
        {
            internal IntPtr Address;
            internal Buffer(string text)
            {
                Address = Marshal.StringToHGlobalUni(text);
            }
            public void Dispose()
            {
                var address = Address;
                Address = IntPtr.Zero;
                if (address != IntPtr.Zero)
                    Marshal.FreeHGlobal(address);
            }
        }
        internal static IDisposable CreateBuffer(string text)
        {
            return new Buffer(text);
        }
        internal static NativeSelectionInsertSendResult Send(IntPtr original, IDisposable resource, int timeoutMs)
        {
            var buffer = resource as Buffer;
            if (buffer == null || buffer.Address == IntPtr.Zero)
                throw new Failure("input_unavailable", "the native selection-edit buffer is unavailable");
            UIntPtr ignored;
            SetLastError(0);
            // EM_REPLACESEL is a system message (<WM_USER): no remote-process
            // allocation, clipboard transaction, posted pointer or input loop.
            IntPtr sent =
                SendMessageTimeout(original, 0xC2, new IntPtr(1), buffer.Address, 2 | 32, (uint)timeoutMs, out ignored);
            int error = Marshal.GetLastWin32Error();
            Native.NoteVisualInput();
            return new NativeSelectionInsertSendResult(
                sent != IntPtr.Zero, sent == IntPtr.Zero ? "native selection-edit call timed out or failed (code " +
                                                               error + "); it may have executed and is not replayed"
                                                         : null);
        }
    }
}
