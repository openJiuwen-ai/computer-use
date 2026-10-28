// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import Foundation

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
    // Native retained requirement, not execute-caller authority or wire data.
    var documentForeground = DocumentForeground.notRequired
    var documentForegroundRequired: Bool { documentForeground.required }

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
