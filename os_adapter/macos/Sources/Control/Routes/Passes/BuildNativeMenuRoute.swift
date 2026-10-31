// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices

/// QUERY: construct only this selected native route; PREPARE/DISPATCH is deferred to its returned closure.
@MainActor enum BuildNativeMenuRoutePass {
    static func run(_ context: NativeRouteContext, name: String, description: String?) throws -> SelectedNativeRoute {
        let contract = context.resolved.contract
        let binding = context.resolved.binding
        let op = context.operation
        let bound = context.command.target
        let kind = context.command.kind
        let intentKind = context.operation.request.kind
        let foreground = context.command.foreground
        let id = context.command.primitiveID
        let receipt = context.receipt
        let checkBoundary = context.boundary.checkConditions
        let beforeDispatch = context.boundary.beforeDispatch

        let request = try NativeObjectIntent(unit: kind, kind: intentKind, parameters: op.request.parameters)
        guard ["native_action", "prepare_receiver"].contains(kind),
            request.kind != "action" || request.value == name || request.value == description, let node = binding.node
        else { throw DriverError("menu action contract mismatch", code: .invalidRequest) }
        let unit = try context.access.units.menuUnit(bound, within: contract.within)
        if kind == "prepare_receiver" {
            return .prepare {
                if foreground {
                    try op.requireForegroundPreparation(id)
                }
                try await unit.prepare(
                    foreground: foreground, check: checkBoundary, onPreparation: receipt.recordPreparation,
                    onCleanupUnknown: receipt.recordCleanupUnknown)
                receipt.detail = "Selected document menu context prepared; no menu command was sent."
                receipt.confirmation = "confirmed"
            }
        }
        return .dispatch {
            guard unit.window == nil || name == kAXCancelAction || op.preparationConfirmed(id) else {
                throw DriverError(
                    "document menu validation has not been prepared in this operation", code: .inputUnavailable)
            }
            let facts = try context.access.objects.nativeActionFacts(node)
            guard NativeAction.matches(facts.actions, name: name, description: description) else {
                throw DriverError("the selected menu action declaration changed", code: .inputUnavailable)
            }
            try await unit.perform(
                action: name, opensMenu: intentKind == "openMenu", check: checkBoundary, onDispatch: beforeDispatch)
            receipt.detail =
                "Native menu action dispatched in the selected application context; command effect unconfirmed."
            return false
        }

    }
}
