// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

using System;

namespace ComputerUse.Windows
{
    internal static class NativeKeyboardRoute
    {
        internal static bool Contains(IntPtr window, IntPtr owner, IntPtr focus, bool exactUiaFocus)
        {
            if (window == IntPtr.Zero || owner == IntPtr.Zero || focus == IntPtr.Zero || !Native.Inside(window, owner) || !Native.Inside(window, focus))
                return false;
            // A windowless editor can be exposed under a rendering HWND while
            // its native parent hosts the keyboard queue. The reverse relation
            // is valid only with the exact focused UIA leaf, never a focus flag
            // on a container or another HWND merely inside the same window.
            return Native.Inside(owner, focus) || exactUiaFocus && Native.Inside(focus, owner);
        }
    }
}
