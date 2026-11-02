// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

namespace ComputerUse.Windows
{
    internal static class NativeObjectActionPermission
    {
        // ScrollIntoView requests a container viewport change, not interaction
        // with the item's enabled control. Only the original named method
        // grants this exception; automatic click/menu choices keep their gate.
        internal static bool RequiresEnabled(string unit, string action, bool explicitAction)
        {
            return !(explicitAction && unit == "native_action" && action == "ScrollItem.ScrollIntoView");
        }

        internal static bool Allows(bool? enabled, string unit, string action, bool explicitAction)
        {
            return !RequiresEnabled(unit, action, explicitAction) || enabled == true;
        }

        internal static bool Allows(bool? enabled, NativeInputContract contract)
        {
            return Allows(enabled, contract.Unit, contract.Action, contract.ExplicitAction);
        }
    }
}
