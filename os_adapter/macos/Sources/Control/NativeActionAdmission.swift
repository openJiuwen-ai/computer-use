// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import ApplicationServices

/// Shares action admission between offer sampling and the final native call.
/// A focusable control's declared Raise may implement its local focus action;
/// raising an application or window keeps the foreground policy boundary.
enum NativeActionAdmission {
    static func allows(action: String, role: String?, focusWritable: Bool, foreground: Bool, exclusive: Bool) -> Bool {
        guard action == kAXRaiseAction, !foreground, !exclusive else { return true }
        return supportsControlRaise(role: role, focusWritable: focusWritable)
    }

    static func supportsControlRaise(role: String?, focusWritable: Bool) -> Bool {
        guard let role, !role.isEmpty, focusWritable else { return false }
        return ![
            kAXUnknownRole, kAXApplicationRole, kAXWindowRole, kAXSheetRole, kAXDrawerRole, kAXMenuBarRole,
            kAXMenuBarItemRole, kAXMenuRole
        ].contains(role)
    }
}
