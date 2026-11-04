// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

using System;
using System.Diagnostics;

namespace ComputerUse.Windows
{
    internal static class NativePasteConsumers
    {
        sealed class Identity
        {
            internal readonly IntPtr Handle;
            internal readonly uint Pid;
            internal readonly long Started, Generation;
            internal Identity(IntPtr handle)
            {
                Handle = handle;
                Pid = Native.PID(handle);
                Generation = Native.Generation(handle);
                using (var process = Process.GetProcessById((int)Pid)) Started =
                    process.StartTime.ToUniversalTime().Ticks;
                if (!Alive)
                    throw new Failure("input_unavailable",
                                      "the original paste recipient changed while its identity was captured");
            }
            internal bool ProcessMatches(uint pid)
            {
                if (pid != Pid)
                    return false;
                try
                {
                    using (var process = Process.GetProcessById((int)pid)) return process.StartTime.ToUniversalTime()
                            .Ticks == Started;
                }
                catch
                {
                    return false;
                }
            }
            internal bool Alive
            {
                get {
                    return Native.IsWindow(Handle) && Native.PID(Handle) == Pid &&
                           Native.Generation(Handle) == Generation && ProcessMatches(Pid);
                }
            }
        }
        internal static NativePasteConsumer Capture(IntPtr root, IntPtr focus, Request r)
        {
            r.Check();
            Identity window, receiver;
            try
            {
                window = new Identity(root);
                receiver = new Identity(focus);
            }
            catch (Failure)
            {
                throw;
            }
            catch
            {
                throw new Failure(
                    "input_unavailable",
                    "the original paste consumer's native process identity could not be confirmed; no paste sent");
            }
            if (!Native.Inside(root, focus))
                throw new Failure("input_unavailable", "the paste receiver is outside the original window");
            Func<bool> alive = () => window.Alive && receiver.Alive && Native.Inside(root, focus);
            Func<IntPtr, bool> matches = requester =>
            {
                if (!Native.IsWindow(requester) || Native.Focus(root) != focus)
                    return false;
                uint pid = Native.PID(requester);
                if (!window.ProcessMatches(pid) && !receiver.ProcessMatches(pid))
                    return false;
                // A process alone is not a document identity. A root/child or
                // confirmed owner chain is required even for hidden helpers.
                if (Native.Inside(root, requester))
                    return true;
                var current = requester;
                for (int n = 0; n < 32 && current != IntPtr.Zero; n++, current = Native.GetWindow(current, 4))
                    if (current == root)
                        return true;
                return false;
            };
            r.Check();
            return new NativePasteConsumer(alive, matches);
        }
    }
    internal sealed partial class NativeUiaKeyboard : INativePasteConsumerProvider
    {
        public NativePasteConsumer PasteConsumer(NativeKeyboardState state, Request r)
        {
            var target = (Target)state.Native;
            ValidateTarget(target, r);
            var root = target.Window.Native.Handle;
            var focus = Native.Focus(root);
            var consumer = NativePasteConsumers.Capture(root, focus, r);
            ValidateTarget(target, r);
            return consumer;
        }
    }
}
