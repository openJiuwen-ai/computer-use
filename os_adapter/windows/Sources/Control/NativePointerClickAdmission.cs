// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

using System;

namespace ComputerUse.Windows
{
    // PURE: physical reachability and foreground identity are independent.
    // Only one requested left click may naturally activate its destination,
    // without an additional activation gesture. Other routes stay strict.
    internal static class NativePointerClickAdmission
    {
        internal static bool NaturalActivation(NativePointerGestureIntent gesture, bool physicalReady,
                                               bool originalRootHit, bool inputIdle)
        {
            return gesture != null && gesture.CanActivateWindow && physicalReady &&
                   originalRootHit && inputIdle;
        }
        internal static bool ElementScopeAllowsNatural(string[] within)
        {
            return within != null && within.Length == 0;
        }
        internal static bool QueueIdle(bool known, IntPtr capture, uint flags)
        {
            // GUI_CARETBLINKING (1) is compatible with an idle input queue;
            // move-size/menu/system-menu/popup-menu modes are not.
            return known && capture == IntPtr.Zero && (flags & ~1u) == 0;
        }
        internal static bool ElementReady(NativePointerState route, NativeElementHit hit, bool requiresConfirmed)
        {
            return hit.Ready(route.PhysicalReady, requiresConfirmed) &&
                   (route.ForegroundReady || route.NaturalActivation && hit.Confirmed);
        }
    }
}
