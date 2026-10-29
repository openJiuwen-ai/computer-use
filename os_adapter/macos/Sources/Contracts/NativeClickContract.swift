// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import Foundation
import ApplicationServices

/// Immutable v1 offer retained by the original primitive store. The wire is
/// a selection projection; native authority and delivery are never supplied
/// by the execute caller.
struct NativeClickContract: Equatable {
    enum Admission: String { case ready, preparationRequired, unavailable, insufficientEvidence }
    enum DocumentForeground: Equatable {
        case notRequired, required(ready: Bool), unconfirmed(ready: Bool)
        var required: Bool {
            if case .required = self { return true }
            return false
        }
    }
    let routeFamily: String
    let delivery: NativeClickDelivery
    let admission: Admission
    let reason: String?
    let preparationOptions: [String]
    var backgroundFamily: String? = nil
    var backgroundUnavailable = false
    var popupPointer: NativePopupPointerQualification? = nil
    // Native retained requirement, not execute-caller authority or wire data.
    var documentForeground = DocumentForeground.notRequired
    var documentForegroundRequired: Bool { documentForeground.required }

    static func == (lhs: Self, rhs: Self) -> Bool {
        let popupMatches: Bool
        switch (lhs.popupPointer, rhs.popupPointer) {
        case (nil, nil): popupMatches = true
        case (let a?, let b?): popupMatches = a.matches(b, same: { CFEqual($0, $1) })
        default: popupMatches = false
        }
        return popupMatches && lhs.routeFamily == rhs.routeFamily && lhs.delivery == rhs.delivery
            && lhs.admission == rhs.admission && lhs.reason == rhs.reason
            && lhs.preparationOptions == rhs.preparationOptions && lhs.backgroundFamily == rhs.backgroundFamily
            && lhs.backgroundUnavailable == rhs.backgroundUnavailable && lhs.documentForeground == rhs.documentForeground
    }

    /// A refresh of the same retained selection cannot erase proved Web
    /// foreground requirements merely because a parent getter is unavailable.
    func retainingDocumentForeground(from previous: Self?) -> Self {
        guard previous?.documentForegroundRequired == true, case .unconfirmed(let ready) = documentForeground else {
            return self
        }
        return Self(
            routeFamily: routeFamily, delivery: delivery,
            admission: !ready && admission == .ready ? .preparationRequired : admission, reason: reason,
            preparationOptions: ready ? preparationOptions : ["foreground"],
            backgroundFamily: ready ? backgroundFamily : nil, backgroundUnavailable: backgroundUnavailable,
            documentForeground: .required(ready: ready))
    }

    var wire: [String: Any] {
        [
            "routeFamily": routeFamily,
            "interaction": [.objectAction, .objectSelection].contains(delivery) ? "semantic" : "pointer",
            "delivery": delivery.rawValue, "admission": admission.rawValue, "reason": reason as Any? ?? NSNull(),
            "preparationOptions": preparationOptions, "preferenceProfile": "default"
        ]
    }
}
