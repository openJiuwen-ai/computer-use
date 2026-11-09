// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

using System;

namespace ComputerUse.Windows
{
    internal sealed class NativeWindowEnded : Failure
    {
        internal NativeWindowEnded(string detail) : base("stale_target", detail) { }
    }

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
            uint observedPid = Native.PID(Handle);
            if (!Native.IsWindow(Handle) || Native.Generation(Handle) != Generation ||
                observedPid != 0 && observedPid != Pid)
                throw new NativeWindowEnded("the selected window closed or was replaced; no other document was selected");
            if (observedPid == 0)
                throw new Failure("stale_target", "the selected window's native process could not be verified");
            try
            {
                NativeProcessLifetime.Require(Pid, Started);
            }
            catch (NativeWindowEnded)
            {
                throw;
            }
            catch
            {
                throw new Failure("stale_target", "the selected application process could not be verified");
            }
        }
    }
}
