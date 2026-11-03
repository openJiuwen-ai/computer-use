// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import ApplicationServices

/// Declared content interaction may admit background preparation. Point-only
/// qualifications retain their exact foreground-window requirement; an AX
/// provider omitting children cannot by itself require global mouse injection.
struct NativeAddressedPointerReceiver {
    enum Qualification: String {
        case unconfirmed, declaredInteraction, sameWindowPoint, originalWindowPoint
        case originalElementExactHit, originalElementNeedsExposure

        func requirement(captured: Bool) -> NativePointerReadiness.Requirement? {
            switch self {
            case .declaredInteraction: return .contentReceiver
            case .sameWindowPoint, .originalWindowPoint: return captured ? .foregroundWindowPoint : nil
            case .originalElementExactHit, .originalElementNeedsExposure:
                return captured ? nil : .foregroundWindowPoint
            case .unconfirmed: return nil
            }
        }
    }
    let role: String?
    let actions: [String]?
    var focusWritable = false
    var selectedTextWritable = false
    var valueWritable = false

    /// One role-scoped query for both retained elements and AX point hits. A
    /// previously sampled focus attribute can be reused, but it does not erase
    /// independent selected-text or ranged-value interaction declarations.
    static func sample(
        role: String?, actions: [String]?, focusWritable: Bool? = nil, writable: (String) -> Bool
    ) -> Self {
        var result = Self(role: role, actions: actions)
        switch role {
        case kAXTextFieldRole, kAXTextAreaRole, kAXComboBoxRole:
            result.focusWritable = focusWritable ?? writable(kAXFocusedAttribute)
            if !result.focusWritable { result.selectedTextWritable = writable(kAXSelectedTextAttribute) }
        case kAXSliderRole, kAXIncrementorRole:
            result.valueWritable = writable(kAXValueAttribute)
        default: break
        }
        return result
    }

    @MainActor static func query(
        _ element: AXUIElement, role: String?, actions: [String]?, focusWritable: Bool? = nil
    ) -> Self {
        sample(role: role, actions: actions, focusWritable: focusWritable) {
            AXAccess.writable(element, $0).value == true
        }
    }

    /// A scroll preparation point is not the requested click receiver. For a
    /// retained original element, allow exposure before its final hit can be
    /// sampled. Undeclared controls keep the foreground/exact-original-hit
    /// requirement; this is not evidence that the hidden target is already hit.
    var exposureQualification: Qualification {
        supported ? .declaredInteraction : .originalElementNeedsExposure
    }

    func qualification(concreteHit: Bool, sameWindow: Bool) -> Qualification {
        guard concreteHit, sameWindow else { return .unconfirmed }
        return supported ? .declaredInteraction : .sameWindowPoint
    }

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
