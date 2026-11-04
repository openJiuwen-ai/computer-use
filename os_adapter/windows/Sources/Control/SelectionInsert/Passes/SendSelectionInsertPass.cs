// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

using System;
using System.Collections.Generic;

namespace ComputerUse.Windows
{
    internal static class SendSelectionInsertPass
    {
        internal const string Effects = "DISPATCH";
        internal static void Run(INativeSelectionInsertSequence sequence, IDisposable buffer,
                                 NativeWriteOperation operation, Request request, Dictionary<string, object> result)
        {
            request.Check();
            int timeout = (int)Math.Max(1, Math.Min(250, request.Deadline - Wire.Now));
            request.Sending();
            operation.Dispatched = true;
            result["dispatch"] = "unknown";
            result["retryReason"] = null;
            NativeSelectionInsertSendResult sent;
            try
            {
                sent = sequence.Send(buffer, timeout);
            }
            catch (Exception error)
            {
                throw new Failure("execution_unknown",
                                  "native selection-edit call failed after entry; effect unknown, not replayed: " +
                                      error.GetType().Name + ": " + error.Message);
            }
            if (sent == null || !sent.Completed)
                throw new Failure(
                    "execution_unknown",
                    "native selection-edit call timed out or failed; it may have changed text and was not replayed" +
                        (sent == null || String.IsNullOrEmpty(sent.Detail) ? "." : ": " + sent.Detail));
            result["dispatch"] = "dispatched";
            result["confirmation"] = "unavailable";
            result["detail"] =
                "Native current-selection text edit dispatched to the original Edit; no physical keys, clipboard paste or submission was sent. Read to verify text, selection and application effect.";
        }
    }
}
