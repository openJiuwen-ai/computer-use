// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

using System;
using System.Collections.Generic;

namespace ComputerUse.Windows
{
    internal static class SendCharacterUnitPass
    {
        internal const string Effects = "DISPATCH";
        internal static NativeCharacterMessageResult Run(INativeCharacterTextSequence sequence, char unit,
                                                         NativeWriteOperation operation, Request request,
                                                         Dictionary<string, object> result)
        {
            request.Check();
            int timeout = (int)Math.Max(1, Math.Min(250, request.Deadline - Wire.Now));
            request.Sending();
            operation.Dispatched = true;
            result["dispatch"] = "unknown";
            try
            {
                return sequence.SendCharacter(unit, timeout);
            }
            catch (Exception error)
            {
                return new NativeCharacterMessageResult(false, error.GetType().Name + ": " + error.Message);
            }
        }
    }
}
