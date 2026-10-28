// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

using System;
using System.Collections.Generic;

namespace ComputerUse.Windows
{
    internal static class ReleaseSelectionInsertBufferPass
    {
        internal const string Effects = "RESOURCE";
        internal static void Run(IDisposable buffer, NativeWriteOperation operation, Dictionary<string, object> result)
        {
            if (buffer == null)
                return;
            try
            {
                buffer.Dispose();
            }
            catch
            {
                operation.Cleanup = "failed";
                result["cleanup"] = "failed";
                throw;
            }
        }
    }
}
