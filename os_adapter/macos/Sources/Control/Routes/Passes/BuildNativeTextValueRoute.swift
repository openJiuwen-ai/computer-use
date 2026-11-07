// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices

/// QUERY: construct only this selected native route; PREPARE/DISPATCH is deferred to its returned closure.
@MainActor enum BuildNativeTextValueRoutePass {
    static func run(_ context: NativeRouteContext, valueContract: NativeElementControl.TextValueContract) throws
        -> SelectedNativeRoute
    {
        let binding = context.resolved.binding
        let op = context.operation
        let kind = context.command.kind
        let intentKind = context.operation.request.kind
        let receipt = context.receipt
        func beforeDispatch() throws {
            guard case .geckoAtomicFullReplacement(let proof) = valueContract.semantics else {
                return try context.boundary.beforeDispatch()
            }
            try context.boundary.checkConditions()
            // AXValue can take focus. Recheck after the scope queries and
            // before marking any requested input in flight.
            guard NativeGeckoTextProvider.focused(proof) else {
                throw NativeRouteUnavailable(description: "the original replacement receiver lost focus before dispatch")
            }
            try context.boundary.markDispatchInFlight()
        }

        return .dispatch {
            let request = try NativeObjectIntent(unit: kind, kind: intentKind, parameters: op.request.parameters)
            guard kind == valueContract.unit, let text = request.value, let node = binding.node else {
                throw DriverError("native text contract mismatch", code: .invalidRequest)
            }
            let edited = try context.access.objects.executeNativeTextValue(
                node, contract: valueContract, text: text, onDispatch: beforeDispatch,
                originalWindow: { try context.access.scopes.fixedInputWindow(context.command.target)?.window })
            receipt.detail = edited.detail
            receipt.confirmation = edited.verified ? "confirmed" : "unavailable"
            return false
        }

    }
}
