// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import ApplicationServices

/// A concrete control with an advertised interaction, not merely a child of
/// an AX window. Missing evidence leaves physical input available independently.
struct NativeAddressedPointerReceiver {
    let role: String?
    let actions: [String]?
    var focusWritable = false
    var selectedTextWritable = false
    var valueWritable = false

    var supported: Bool {
        guard let role else { return false }
        let names = Set(actions ?? [])
        switch role {
        case kAXButtonRole, kAXCheckBoxRole, kAXRadioButtonRole, kAXMenuItemRole, "AXLink":
            return names.contains(kAXPressAction) || names.contains("AXPick")
        case kAXMenuButtonRole, kAXPopUpButtonRole:
            return names.contains(kAXPressAction) || names.contains(kAXShowMenuAction)
        case kAXTextFieldRole, kAXTextAreaRole, kAXComboBoxRole: return focusWritable || selectedTextWritable
        case kAXSliderRole, kAXIncrementorRole:
            return valueWritable && (names.contains(kAXIncrementAction) || names.contains(kAXDecrementAction))
        default: return false
        }
    }
}
