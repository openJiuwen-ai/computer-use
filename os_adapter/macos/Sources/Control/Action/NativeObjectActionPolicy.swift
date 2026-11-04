// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import ApplicationServices

/// Query-only applicability of optional context for an addressed object action.
enum NativeObjectActionPolicy {
    static func applicationContextEligible(action: String, hasWindow: Bool, applicationMenu: Bool) -> Bool {
        [kAXPressAction, kAXShowMenuAction].contains(action) && hasWindow && !applicationMenu
    }
}
