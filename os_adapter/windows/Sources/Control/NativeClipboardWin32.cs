// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

using System;
using System.ComponentModel;
using System.Runtime.InteropServices;
using System.Text;

namespace ComputerUse.Windows
{
    internal sealed class NativeWindowsClipboard : INativeClipboardProvider
    {
        public INativeClipboardPublication Create()
        {
            return new NativeClipboardWin32();
        }
    }

    internal sealed class NativeClipboardWin32 : INativeClipboardPublication
    {
        const uint UnicodeText = 13, Moveable = 2;
        readonly NativeClipboardOwner actor;
        bool opened, published;
        volatile bool changed, releaseFailed;
        public bool Changed { get { return changed; } }

        internal NativeClipboardWin32()
        {
            actor = new NativeClipboardOwner(Close);
        }

        public void Publish(string text, Request r)
        {
            NativePaste.Validate(text);
            // CF_UNICODETEXT uses CRLF and a terminating UTF-16 NUL.
            var bytes = Encoding.Unicode.GetBytes(text.Replace("\r\n", "\n").Replace("\r", "\n")
                                                     .Replace("\n", "\r\n") + "\0");
            double until = Math.Min(r.Deadline, Wire.Now + 400);
            while (!actor.Invoke(() => TryPublish(bytes, r)))
            {
                if (Wire.Now >= until)
                    throw new Failure("input_unavailable", "clipboard is busy; no paste sent");
                r.Wait(10);
            }
        }

        bool TryPublish(byte[] bytes, Request r)
        {
            r.Check();
            if (changed)
                throw new Failure("invalid_request", "clipboard publication has already been used");
            if (!OpenClipboard(actor.Handle))
                return false;
            opened = true;
            IntPtr memory = IntPtr.Zero;
            try
            {
                // Allocate before touching the existing clipboard. Once it
                // has been cleared, complete the publication without a
                // cancellation point between EmptyClipboard and SetClipboardData.
                memory = GlobalAlloc(Moveable, new UIntPtr((uint)bytes.Length));
                if (memory == IntPtr.Zero)
                    throw Error("clipboard allocation failed");
                IntPtr pointer = GlobalLock(memory);
                if (pointer == IntPtr.Zero)
                    throw Error("clipboard allocation could not be locked");
                try
                {
                    Marshal.Copy(bytes, 0, pointer, bytes.Length);
                }
                finally
                {
                    GlobalUnlock(memory);
                }
                r.Check();
                if (!EmptyClipboard())
                    throw Error("clipboard could not be cleared");
                changed = true;
                r.Effect = true;
                if (SetClipboardData(UnicodeText, memory) == IntPtr.Zero)
                    throw Error("clipboard was cleared but text could not be published");
                memory = IntPtr.Zero; // Win32 owns the eager data past owner exit.
                published = true;
                return true;
            }
            finally
            {
                if (memory != IntPtr.Zero && GlobalFree(memory) != IntPtr.Zero)
                    releaseFailed = true;
                Close();
            }
        }

        public void Validate(Request r)
        {
            r.Check();
            // A reader does not need to identify itself or relinquish the
            // clipboard lock. A new writer takes ownership on EmptyClipboard.
            if (!published || GetClipboardOwner() != actor.Handle)
                throw new Failure("input_unavailable", "clipboard changed before the paste chord; no paste sent");
        }

        void Close()
        {
            if (!opened)
                return;
            if (!CloseClipboard())
                throw Error("could not release clipboard lock");
            opened = false;
        }

        public void Dispose()
        {
            actor.Dispose();
            if (releaseFailed)
                throw new NativeClipboardCleanupFailure("clipboard allocation could not be released");
        }

        static Failure Error(string detail)
        {
            return new Failure("input_unavailable",
                               detail + ": " + new Win32Exception(Marshal.GetLastWin32Error()).Message);
        }

        [DllImport("user32.dll", SetLastError = true)]
        static extern bool OpenClipboard(IntPtr owner);
        [DllImport("user32.dll", SetLastError = true)]
        static extern bool CloseClipboard();
        [DllImport("user32.dll", SetLastError = true)]
        static extern bool EmptyClipboard();
        [DllImport("user32.dll")]
        static extern IntPtr GetClipboardOwner();
        [DllImport("user32.dll", SetLastError = true)]
        static extern IntPtr SetClipboardData(uint format, IntPtr data);
        [DllImport("kernel32.dll", SetLastError = true)]
        static extern IntPtr GlobalAlloc(uint flags, UIntPtr bytes);
        [DllImport("kernel32.dll", SetLastError = true)]
        static extern IntPtr GlobalLock(IntPtr handle);
        [DllImport("kernel32.dll", SetLastError = true)]
        static extern bool GlobalUnlock(IntPtr handle);
        [DllImport("kernel32.dll", SetLastError = true)]
        static extern IntPtr GlobalFree(IntPtr handle);
    }
}
