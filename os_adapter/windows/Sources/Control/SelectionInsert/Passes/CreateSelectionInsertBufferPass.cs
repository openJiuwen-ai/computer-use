// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

using System;

namespace ComputerUse.Windows
{
    internal static class CreateSelectionInsertBufferPass
    {
        internal const string Effects = "RESOURCE";
        internal static IDisposable Run(INativeSelectionInsertSequence sequence, string text)
        {
            var buffer = sequence.CreateBuffer(text);
            if (buffer == null)
                throw new Failure("input_unavailable",
                                  "native selection-edit buffer was not created; no text was sent");
            return buffer;
        }
    }
}
