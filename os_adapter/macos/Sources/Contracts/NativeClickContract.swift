// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import Foundation
import ApplicationServices

/// Immutable v1 offer retained by the original primitive store. The wire is
/// a selection projection; native authority and delivery are never supplied
/// by the execute caller.
struct NativeClickContract: Equatable {
    enum Admission: String { case ready, preparationRequired, unavailable, insufficientEvidence }
    let routeFamily: String
    let delivery: NativeClickDelivery
    let admission: Admission
    let reason: String?
    let preparationOptions: [String]
    var backgroundFamily: String? = nil
    var backgroundUnavailable = false
    var popupPointer: NativePopupPointerQualification? = nil
    var qtComboChoice: NativeQtComboChoice? = nil
    var coordinateMenuPress: NativeCoordinateMenuPress? = nil
    var clippedLeaf: NativeClickClippedLeaf? = nil
    var pointerRequirement: NativePointerReadiness.Requirement = .contentReceiver
    /// Original-element geometry must be confirmed in the exposed foreground
    /// window, even when addressed transport already has a background lease.
    var requiresForegroundGeometry = false
    /// Opaque row contents qualify by an exact original hit, independently
    /// of whether their retained window is foreground.
    var requiresExactElementHit = false

    /// The retained native offer, never an execute field, owns this requirement.
    /// A qualified popup or member keeps its separate existing admission proof.
    func permitsElementPointer(hasActuator: Bool) -> Bool {
        guard popupPointer == nil else { return false }
        guard clippedLeaf == nil || delivery == .windowDirected && !hasActuator else { return false }
        guard !requiresExactElementHit || delivery == .windowDirected && !hasActuator else { return false }
        guard !requiresForegroundGeometry || delivery == .windowDirected && !hasActuator else { return false }
        return pointerRequirement == .contentReceiver || delivery == .windowDirected && !hasActuator
    }

    static func == (lhs: Self, rhs: Self) -> Bool {
        let popupMatches: Bool
        switch (lhs.popupPointer, rhs.popupPointer) {
        case (nil, nil): popupMatches = true
        case (let a?, let b?): popupMatches = a.matches(b, same: { CFEqual($0, $1) })
        default: popupMatches = false
        }
        let comboMatches: Bool
        switch (lhs.qtComboChoice, rhs.qtComboChoice) {
        case (nil, nil): comboMatches = true
        case (let a?, let b?): comboMatches = a.matches(b)
        default: comboMatches = false
        }
        let coordinateMatches: Bool
        switch (lhs.coordinateMenuPress, rhs.coordinateMenuPress) {
        case (nil, nil): coordinateMatches = true
        case (let a?, let b?): coordinateMatches = a.matches(b)
        default: coordinateMatches = false
        }
        return coordinateMatches && comboMatches && popupMatches && lhs.clippedLeaf == rhs.clippedLeaf
            && lhs.routeFamily == rhs.routeFamily && lhs.delivery == rhs.delivery
            && lhs.admission == rhs.admission && lhs.reason == rhs.reason
            && lhs.preparationOptions == rhs.preparationOptions && lhs.backgroundFamily == rhs.backgroundFamily
            && lhs.backgroundUnavailable == rhs.backgroundUnavailable
            && lhs.pointerRequirement == rhs.pointerRequirement
            && lhs.requiresForegroundGeometry == rhs.requiresForegroundGeometry
            && lhs.requiresExactElementHit == rhs.requiresExactElementHit
    }

    var wire: [String: Any] {
        let value: [String: Any] = [
            "routeFamily": routeFamily,
            "interaction": [.objectAction, .objectSelection].contains(delivery) ? "semantic" : "pointer",
            "delivery": delivery.rawValue, "admission": admission.rawValue, "reason": reason as Any? ?? NSNull(),
            "preparationOptions": preparationOptions, "preferenceProfile": "default"
        ]
        return value
    }
}
