// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

using System;

namespace ComputerUse.Windows
{
    // Retained native window lifetime, shared by read and execute. No legacy
    // observation, input receiver or control strategy is stored here.
    internal sealed class Window
    {
        internal IntPtr Handle;
        internal uint Pid;
        internal long Started, Generation;
        internal string Ref, Scope, Selector, Name;
        internal void Validate()
        {
            if (!Native.IsWindow(Handle) || Native.PID(Handle) != Pid || Native.Generation(Handle) != Generation)
                throw new Failure("stale_target",
                                  "the selected window closed or was replaced; no other document was selected");
            try
            {
                NativeProcessLifetime.Require(Pid, Started);
            }
            catch
            {
                throw new Failure("stale_target", "the selected application process ended or changed");
            }
        }
    }
}
