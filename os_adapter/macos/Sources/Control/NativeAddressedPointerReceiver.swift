// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import ApplicationServices

/// Distinguish declared content interaction from a concrete hit in the original
/// window from an exact hit on the original element. Both geometric proofs
/// require the actual foreground window; neither grants background capability.
struct NativeAddressedPointerReceiver {
    enum Qualification: String {
        case unconfirmed, declaredInteraction, sameWindowPoint, originalElementExactHit, originalElementNeedsExposure

        func requirement(captured: Bool) -> NativePointerReadiness.Requirement? {
            switch self {
            case .declaredInteraction: return .contentReceiver
            case .sameWindowPoint: return captured ? .foregroundWindowPoint : nil
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
