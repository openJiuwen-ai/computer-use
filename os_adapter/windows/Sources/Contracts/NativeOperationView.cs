// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

using System;
using System.Collections.Generic;
using System.Linq;

namespace ComputerUse.Windows
{
    internal sealed class NativeOperationView
    {
        internal readonly string Operation;
        internal readonly Dictionary<string, object> Original;
        internal readonly NativeWriteOperation Write;
        internal readonly double Deadline;
        internal NativeOperationView(string operation, Dictionary<string, object> original, NativeWriteOperation write, double deadline)
        {
            Operation = operation;
            Original = original;
            Write = write;
            Deadline = deadline;
        }
    }
}
