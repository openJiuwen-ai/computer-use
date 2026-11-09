// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

using System;

namespace ComputerUse.Windows
{
    internal interface INativeClipboardProvider
    {
        INativeClipboardPublication Create();
    }

    internal interface INativeClipboardPublication : IDisposable
    {
        // Changed is set as soon as the old contents are cleared, even if
        // publication later fails. Disposing never rolls back the clipboard.
        bool Changed { get; }
        void Publish(string text, Request request);
        void Validate(Request request);
    }

    internal sealed class NativeClipboardCleanupFailure : Exception
    {
        internal NativeClipboardCleanupFailure(string detail) : base(detail)
        {
        }
    }
}
