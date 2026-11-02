// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

namespace ComputerUse.Windows
{
    // A query about one named pattern method, never a complete action catalogue.
    internal sealed class NativeExactActionEvidence
    {
        internal readonly string Name;
        internal readonly bool Available;
        internal NativeExactActionEvidence(string name, bool available)
        {
            Name = name;
            Available = available;
        }
    }
}
