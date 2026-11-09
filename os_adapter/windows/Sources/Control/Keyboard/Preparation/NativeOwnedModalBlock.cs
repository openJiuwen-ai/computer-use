// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

using System;

namespace ComputerUse.Windows
{
    internal enum NativeOwnedModalState { Pending, Cleared, Changed }

    // The public refusal stays blocked_by_modal. Only an addressed key's
    // in-flight foreground preparation consumes this local, live evidence.
    internal sealed class NativeOwnedModalBlock : Failure
    {
        internal readonly NativeWindowRecord Original;
        internal readonly Func<Request, bool, NativeOwnedModalState> Sample;
        internal bool AfterActivation;

        internal NativeOwnedModalBlock(NativeWindowRecord original, Func<Request, bool, NativeOwnedModalState> sample)
            : base("blocked_by_modal", "an owned native modal window blocks the selected control")
        {
            Original = original;
            Sample = sample;
        }
    }
}
