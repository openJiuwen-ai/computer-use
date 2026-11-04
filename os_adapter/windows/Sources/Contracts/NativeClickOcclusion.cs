// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

using System;

namespace ComputerUse.Windows
{
    // Read-only evidence of one physical foreign-root obstruction, never App membership.
    internal sealed class NativeClickOcclusion
    {
        internal readonly IntPtr ForeignRoot;
        internal readonly NativeClickTargetCondition TargetCondition;
        internal NativeClickOcclusion(IntPtr root, NativeClickTargetCondition condition = null)
        {
            ForeignRoot = root;
            TargetCondition = condition;
        }
    }
    internal sealed class NativeClickTargetCondition
    {
        readonly Action<Request> require;
        internal NativeClickTargetCondition(Action<Request> require)
        {
            if (require == null)
                throw new ArgumentNullException("require");
            this.require = require;
        }
        internal void Require(Request request)
        {
            require(request);
        }
    }
}
