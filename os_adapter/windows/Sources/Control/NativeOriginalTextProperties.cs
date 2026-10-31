// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

using System.Windows.Automation;

namespace ComputerUse.Windows
{
    // A per-scalar query result only. Never retained as permission to dispatch.
    internal sealed class NativeOriginalTextProperties
    {
        internal readonly ControlType Role;
        internal readonly bool? Enabled;
        NativeOriginalTextProperties(ControlType role, bool? enabled)
        {
            Role = role;
            Enabled = enabled;
        }

        internal static NativeOriginalTextProperties Read(UiaElement original, Request r)
        {
            r.Check();
            // Cache request is Element/Full, only these two properties. The
            // original binding is not replaced by the returned interface.
            var snapshot = original.Snapshot(UiaClient.Cache(
                new[] { AutomationElement.ControlTypeProperty.Id, AutomationElement.IsEnabledProperty.Id }));
            r.Check();
            var role = snapshot.GetCachedPropertyValue(AutomationElement.ControlTypeProperty, true) as ControlType;
            r.Check();
            var enabled = snapshot.GetCachedPropertyValue(AutomationElement.IsEnabledProperty, true) as bool ? ;
            r.Check();
            return new NativeOriginalTextProperties(role, enabled);
        }
    }
}
