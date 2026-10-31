// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

using System;

namespace ComputerUse.Windows
{
    // Typed resource-health evidence, not a normal optional-capability refusal.
    // The existing operation/runtime owners alone retain retirement state.
    internal sealed class NativeDesktopCleanupFailure : Exception
    {
        internal readonly string Reason;
        internal Failure OriginalFailure
        {
            get {
                Exception current = InnerException;
                while (current is NativeDesktopCleanupFailure)
                    current = current.InnerException;
                return current as Failure;
            }
        }
        internal NativeDesktopCleanupFailure(string reason, Exception primary = null)
            : base("Interactive desktop query resource cleanup is unconfirmed: " + reason +
                       (primary == null ? "" : "; original query: " + primary.GetType().Name + ": " + primary.Message),
                   primary)
        {
            Reason = reason;
        }
    }
}
