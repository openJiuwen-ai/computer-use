// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

using System;
using System.Collections.Generic;

namespace ComputerUse.Windows
{
    // One Unicode scalar, one or two synchronous messages to a frozen HWND.
    // The existing operation owns dispatch state; this pipeline owns no retry.
    internal static class NativeCharacterDispatchPipeline
    {
        internal static void Run(INativeCharacterTextSequence sequence, NativeTextScalar scalar,
                                 NativeWriteOperation operation, Request request, Dictionary<string, object> result,
                                 Action guard)
        {
            CheckCharacterIdlePass.Run(sequence, request);
            guard();
            request.Check();
            int completed = 0;
            bool uncertain = false;
            try
            {
                for (int index = 0; index < scalar.Utf16.Length; index++)
                {
                    if (index != 0)
                        CheckCharacterContinuationPass.Run(sequence, request);
                    var receipt = SendCharacterUnitPass.Run(sequence, scalar.Utf16[index], operation, request, result);
                    if (receipt == null || !receipt.Completed)
                    {
                        uncertain = true;
                        throw new Failure(
                            "execution_unknown",
                            "the directed character message timed out or failed; it may have been processed and was not replayed" +
                                (receipt == null || String.IsNullOrEmpty(receipt.Detail) ? "."
                                                                                         : ": " + receipt.Detail));
                    }
                    completed++;
                }
                result["dispatch"] = "dispatched";
            }
            catch
            {
                if (completed > 0 && !uncertain)
                    result["dispatch"] = "partial";
                request.Check();
                throw;
            }
        }
    }
}
