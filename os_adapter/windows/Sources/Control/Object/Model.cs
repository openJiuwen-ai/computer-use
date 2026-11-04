// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

using System;
using System.Collections.Generic;
using System.Linq;

namespace ComputerUse.Windows
{
    internal enum NativeObjectEvidenceRequirement
    {
        SemanticSnapshot,
        ExactAction,
        ValueReplacement
    }

    internal sealed class ObjectDispatchPlan
    {
        internal readonly NativeInputContract Contract;
        internal readonly Dictionary<string, object> Target;
        internal readonly string Unit, Text;
        internal readonly NativeObjectEvidenceRequirement Evidence;
        internal ObjectDispatchPlan(NativeInputContract contract, Dictionary<string, object> target, string unit,
                                    string text, NativeObjectEvidenceRequirement evidence)
        {
            Contract = contract;
            Target = target;
            Unit = unit;
            Text = text;
            Evidence = evidence;
        }
    }
}
