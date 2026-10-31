// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

using System;
using System.Collections.Generic;
using System.Linq;

namespace ComputerUse.Windows
{
    // Pure observation-prefix check. An incomplete relation still has an
    // observed suffix worth delivering, but never becomes input authority or
    // proof that all logical children were collected.
    internal static class NativeReadRelationPaging
    {
        internal static bool NeedsCapturedMembership(NativeNodeRecord target)
        {
            return String.IsNullOrEmpty(target.Runtime) || target.ObservationOnly;
        }
        internal static bool CanContinue(string[] previous, int offset, bool previouslyComplete, IList<string> current,
                                         bool complete)
        {
            return (!previouslyComplete || complete) && current.Count == previous.Length &&
                   current.Take(offset).SequenceEqual(previous.Take(offset));
        }
    }
}
