// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

using System;

namespace ComputerUse.Windows
{
    // A frozen native recipient. These callbacks query only retained Win32
    // identities; they never run UIA, choose a window, focus or dispatch input.
    internal sealed class NativePasteConsumer
    {
        readonly Func<bool> alive;
        readonly Func<IntPtr, bool> matches;
        internal NativePasteConsumer(Func<bool> alive, Func<IntPtr, bool> matches)
        {
            this.alive = alive;
            this.matches = matches;
        }
        internal bool Alive
        {
            get {
                return alive();
            }
        }
        internal bool Matches(IntPtr window)
        {
            return window != IntPtr.Zero && alive() && matches(window);
        }
    }
    internal interface INativePasteConsumerProvider
    {
        NativePasteConsumer PasteConsumer(NativeKeyboardState state, Request request);
    }
    internal enum NativePasteHandoff
    {
        TargetRead,
        Unconfirmed,
        OwnershipLost,
        TargetLost
    }
    internal sealed class NativeClipboardCleanupFailure : Exception
    {
        internal NativeClipboardCleanupFailure(string detail) : base(detail)
        {
        }
    }
}
