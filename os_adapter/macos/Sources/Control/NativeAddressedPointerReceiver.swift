// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import ApplicationServices

/// Declared content interaction may admit background preparation. Point-only
/// qualifications retain their exact foreground-window requirement; an AX
/// provider omitting children cannot by itself require global mouse injection.
struct NativeAddressedPointerReceiver {
    enum Qualification: String {
        case unconfirmed, declaredInteraction, sameWindowPoint, originalWindowPoint
        case originalElementExactHit, originalElementNeedsExposure
        case originalElementForegroundGeometry

        var requiresForegroundGeometry: Bool { self == .originalElementForegroundGeometry }

        func requirement(captured: Bool) -> NativePointerReadiness.Requirement? {
            switch self {
            case .declaredInteraction: return .contentReceiver
            case .sameWindowPoint, .originalWindowPoint: return captured ? .foregroundWindowPoint : nil
            case .originalElementExactHit, .originalElementNeedsExposure:
                return captured ? nil : .foregroundWindowPoint
            case .originalElementForegroundGeometry: return captured ? nil : .contentReceiver
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

    /// Compound text declares interaction on the owner. Its qualified passive
    /// caption supplies the right-click point; dispatch remains a mouse pair.
    static func compoundTextContextQualification(
        intent: NativeIntent, ownerRole: String?, ownerActions: [String]?, memberRole: String?, exactMemberHit: Bool
    ) -> Qualification? {
        guard intent.kind == "click", intent.parameters["button"] as? String == "right",
            ownerRole == kAXStaticTextRole, ownerActions?.contains(kAXPressAction) == true,
            [kAXStaticTextRole, "AXHeading"].contains(memberRole ?? ""), exactMemberHit
        else { return nil }
        return .declaredInteraction
    }

    /// A retained control can need window preparation before its provider
    /// exposes a usable hit. Admission preserves that obligation; it does not
    /// claim that the coarse window is already the requested receiver. Final
    /// element geometry retains the same parent/frame proof as physical input.
    func retainedElementQualification(
        basis: NativeElementHitSelection.Basis, preparation: NativeElementHitSelection.Preparation,
        exactOriginalHit: Bool, popup: Bool
    ) -> Qualification? {
        if basis == .exactHit, exactOriginalHit {
            return supported ? .declaredInteraction : popup ? .unconfirmed : .originalElementExactHit
        }
        guard !popup else { return nil }
        if basis == .ownedControlPart {
            return supported ? .declaredInteraction : .originalElementForegroundGeometry
        }
        if basis == .coarseAncestorGeometry || basis == .serviceAncestorGeometry || preparation == .foreground {
            return .originalElementForegroundGeometry
        }
        return nil
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
