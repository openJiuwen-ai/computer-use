// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices

/// QUERY: construct only this selected native route; PREPARE/DISPATCH is deferred to its returned closure.
@MainActor enum BuildNativeActionRoutePass {
    static func run(_ context: NativeRouteContext, name: String, description: String?) throws -> SelectedNativeRoute {
        let binding = context.resolved.binding
        let op = context.operation
        let kind = context.command.kind
        let intentKind = context.operation.request.kind
        let beforeDispatch = context.boundary.beforeDispatch

        return .dispatch {
            let request = try NativeObjectIntent(unit: kind, kind: intentKind, parameters: op.request.parameters)
            guard kind == "native_action",
                request.kind != "action" || request.value == name || request.value == description,
                let node = binding.node
            else { throw DriverError("native action contract mismatch", code: .invalidRequest) }
            try context.access.objects.executeNativeAction(
                node, action: name, description: description, onDispatch: beforeDispatch)
            return false
        }

    }
}
