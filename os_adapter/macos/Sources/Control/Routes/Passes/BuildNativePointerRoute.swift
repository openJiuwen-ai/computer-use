// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices

/// QUERY: construct only this selected native route; PREPARE/DISPATCH is deferred to its returned closure.
@MainActor enum BuildNativePointerRoutePass {
    static func run(_ context: NativeRouteContext, expected: NativePointerIntent.Kind) throws -> SelectedNativeRoute {
        let contract = context.resolved.contract
        let op = context.operation
        let bound = context.command.target
        let kind = context.command.kind
        let intentKind = context.operation.request.kind
        let foreground = context.command.foreground
        let id = context.command.primitiveID
        let receipt = context.receipt
        let checkBoundary = context.boundary.checkConditions
        let markDispatch = context.boundary.markDispatchInFlight

        let pointer = try NativePointerIntent(
            unit: kind, kind: intentKind, parameters: op.request.parameters, foreground: foreground,
            element: !(bound["capture"] is String))
        guard pointer.kind == expected else {
            throw DriverError("pointer primitive intent changed", code: .invalidRequest)
        }
        let captured =
            try (bound["capture"] is String)
            ? NativePointerUnit(capture: context.access.captures.input(bound).entry) : nil
        let element =
            try captured == nil ? context.access.units.elementPointerUnit(bound, actuator: contract.actuator) : nil
        try captured?.validateParameters(pointer)
        if kind == "prepare_receiver" {
            return .prepare {
                if foreground { try op.requireForegroundPreparation(id) }
                if let captured {
                    try await captured.prepare(
                        foreground: foreground, atNative: captured.point(pointer.start!), kind: pointer.kind,
                        check: checkBoundary, onPreparation: receipt.recordPreparation,
                        onCleanupUnknown: receipt.recordCleanupUnknown)
                } else if let element {
                    try await element.prepare(
                        foreground: foreground, check: checkBoundary, onPreparation: receipt.recordPreparation,
                        onCleanupUnknown: receipt.recordCleanupUnknown)
                }
                receipt.detail = "Pointer window route prepared; no requested pointer event sent."
                receipt.confirmation = "confirmed"
            }
        }
        return .dispatch {
            if let captured {
                try await captured.deliver(
                    pointer, check: checkBoundary, onDispatch: markDispatch, onPosted: receipt.recordAccepted,
                    onCleanupUnknown: receipt.recordCleanupUnknown)
            } else if let element {
                try await element.deliver(
                    pointer, check: checkBoundary, onDispatch: markDispatch, onPosted: receipt.recordAccepted,
                    onCleanupUnknown: receipt.recordCleanupUnknown)
            }
            receipt.detail = "\(intentKind) pointer sequence dispatched; application effect unconfirmed."
            return false
        }

    }
}
