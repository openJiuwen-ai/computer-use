// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

using System;
using System.Collections.Generic;
using System.Linq;

namespace ComputerUse.Windows
{
    internal sealed class NativeQueryRequest
    {
        internal readonly string Token;
        internal readonly Dictionary<string, object> Target, Query;
        internal NativeQueryRequest(string token, Dictionary<string, object> target, Dictionary<string, object> query)
        {
            Token = token;
            Target = target;
            Query = query;
        }
    }
}
