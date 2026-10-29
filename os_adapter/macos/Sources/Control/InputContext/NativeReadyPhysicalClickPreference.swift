// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import CoreGraphics

/// PURE: a stronger current sample can change ordering, never input authority.
/// Unknown evidence leaves every ordinary action and visual route intact.
@MainActor enum NativeReadyPhysicalClickPreference {
    static func offer(
        _ original: NativeClickContract, intent: NativeIntent, exclusive: Bool, originalElementHit: Bool,
        window: CGWindowID, sample: NativePointerUnit.PhysicalClickSample
    ) -> NativeClickContract {
        var value = original
        value.preferenceHint = nil
        guard original.delivery == .systemPointer, original.admission == .ready,
            original.preparationOptions.isEmpty, exclusive, window != 0, intent.kind == "click",
            Set(intent.parameters.keys).isSubset(of: ["button", "count"]),
            (intent.parameters["button"] as? String ?? "left") == "left",
            (intent.parameters["count"] as? Int ?? 1) == 1,
            originalElementHit, sample.ready, sample.foreground,
            sample.windowKind == .keyRequiring, sample.focus == .target,
            // AX fallback remains a legal independent pointer route. It does
            // not establish this first cohort's stronger physical preference.
            sample.hit?.windowServer == .window(window)
        else { return value }
        value.preferenceHint = .readyPhysical
        return value
    }
}
