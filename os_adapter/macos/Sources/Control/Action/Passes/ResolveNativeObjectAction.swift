// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import Foundation

/// PURE: validate the immutable object intent before any native preparation.
@MainActor enum ResolveNativeObjectActionPass {
    static func run(unit: String, intent: NativeIntent, node: String?, action: String, description: String?) throws
        -> NativeObjectActionPlan
    {
        let request = try NativeObjectIntent(unit: unit, kind: intent.kind, parameters: intent.parameters)
        guard ["native_action", "prepare_receiver"].contains(unit),
            request.kind != "action" || request.value == action || request.value == description, let node
        else { throw DriverError("native action contract mismatch", code: .invalidRequest) }
        return NativeObjectActionPlan(request: request, node: node)
    }
}
