// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices

/// QUERY: construct only this selected native route; PREPARE/DISPATCH is deferred to its returned closure.
@MainActor enum BuildNativeScrollRoutePass {
    static func run(_ context: NativeRouteContext) throws -> SelectedNativeRoute {
        let contract = context.resolved.contract
        let op = context.operation
        let bound = context.command.target
        let kind = context.command.kind
        let intentKind = context.operation.request.kind
        let foreground = context.command.foreground
        let receipt = context.receipt
        let checkBoundary = context.boundary.checkConditions
        let markDispatch = context.boundary.markDispatchInFlight

        return .dispatch {
            let scroll = try NativeScrollIntent(
                unit: kind, kind: intentKind, parameters: op.request.parameters, foreground: foreground)
            guard let actuator = contract.actuator else {
                throw DriverError("native scroll contract mismatch", code: .invalidRequest)
            }
            let unit = try context.access.units.scrollUnit(bound, actuator: actuator)
            try await unit.perform(
                scroll, check: checkBoundary, onDispatch: markDispatch, onAccepted: receipt.recordAccepted)
            receipt.detail =
                "\(receipt.acceptedCount) native page actions dispatched; resulting content position unconfirmed."
            return false
        }

    }
}
