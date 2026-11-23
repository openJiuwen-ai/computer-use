// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

using System;
using System.Collections.Generic;
using System.Linq;

namespace ComputerUse.Windows
{
    internal sealed class NativeCaptureRequest
    {
        internal readonly Dictionary<string, object> Target;
        internal readonly string Kind;
        internal readonly int Dimension;
        internal readonly bool VisualText;
        internal NativeCaptureRequest(Dictionary<string, object> target, string kind, int dimension, bool visualText)
        {
            Target = target;
            Kind = kind;
            Dimension = dimension;
            VisualText = visualText;
        }
    }
}
