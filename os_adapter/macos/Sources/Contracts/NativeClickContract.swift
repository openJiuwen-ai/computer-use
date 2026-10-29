// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import Foundation

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

    var wire: [String: Any] {
        [
            "routeFamily": routeFamily,
            "interaction": [.objectAction, .objectSelection].contains(delivery) ? "semantic" : "pointer",
            "delivery": delivery.rawValue, "admission": admission.rawValue, "reason": reason as Any? ?? NSNull(),
            "preparationOptions": preparationOptions, "preferenceProfile": "default"
        ]
    }
}
