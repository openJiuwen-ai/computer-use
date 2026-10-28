// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

using System;
using System.Collections.Generic;
using System.Linq;

namespace ComputerUse.Windows
{
    // Failed releases keep their handles until a bounded terminal attempt.
    // Failure is sticky even if a later release succeeds: no new publication
    // can erase an earlier uncertainty in this transaction's resource cleanup.
    internal sealed class NativeClipboardReleaseLedger
    {
        readonly Func<IntPtr, bool> release;
        readonly HashSet<IntPtr> retained = new HashSet<IntPtr>();
        internal bool Failed { get; private set; }
        internal int Retained
        {
            get {
                return retained.Count;
            }
        }
        internal NativeClipboardReleaseLedger(Func<IntPtr, bool> release)
        {
            this.release = release;
        }
        bool TryRelease(IntPtr handle)
        {
            try
            {
                if (release(handle))
                    return true;
            }
            catch
            {
            }
            Failed = true;
            return false;
        }
        internal void Release(IntPtr handle)
        {
            if (handle == IntPtr.Zero || retained.Contains(handle))
                return;
            if (!TryRelease(handle))
                retained.Add(handle);
        }
        internal void TerminalAttempt()
        {
            foreach (var handle in retained.ToArray())
                if (TryRelease(handle))
                    retained.Remove(handle);
        }
    }
}
