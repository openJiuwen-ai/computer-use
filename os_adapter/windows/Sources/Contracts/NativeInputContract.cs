// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

using System;
using System.Collections.Generic;
using System.Linq;

namespace ComputerUse.Windows
{
    internal sealed class NativeInputContract
    {
        internal string Id, Unit, Action, Role;
        internal bool Supported, Text, Replacement;
        internal bool FixedClickChoice;
        internal bool RequireCollapsedExpansion;
        internal NativeClickTargetCondition ClickTargetCondition;
        internal Dictionary<string, object> Target;
        internal string[] Within;
        internal NativePointerPlan Pointer;
        internal NativeSelectionInsertOffer SelectionInsert;
        internal INativeAppInsertPinOwner AppInsertPin;
        internal NativeClickOffer Click;
        internal NativeClickPreparationAuthority ClickAuthority;
    }
}
