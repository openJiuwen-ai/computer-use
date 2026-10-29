// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices

/// QUERY: resolve only the selected v1 contract into borrowed native units.
@MainActor enum ResolveNativeClickPlanPass {
    static func run(
        _ command: NativeUnitCommand, resolved: ResolvedNativeTarget, op: NativeOperation, step: Int64,
        access: NativeControlAccess
    ) throws -> NativeClickPlan {
        let metric = RequestDiagnostics.metrics?.start(.clickPlan)
        defer { metric?.end() }
        let contract = resolved.contract
        guard op.clickContract != nil, op.intent == "click", let offer = contract.click,
            command.kind != "prepare_receiver"
        else {
            throw DriverError("click execution requires a negotiated selected click contract", code: .invalidRequest)
        }
        let target: NativeClickTarget, pointer: NativePointerIntent?
        switch contract.unit {
        case .selection(let selection):
            guard op.clickContract == "v2", command.kind == "native_selection", offer.delivery == .objectSelection,
                contract.actuator == nil, let node = resolved.binding.node
            else { throw DriverError("click selection contract mismatch", code: .invalidRequest) }
            _ = try NativeObjectIntent(unit: command.kind, kind: "click", parameters: op.request.parameters)
            target = .selection(node: node, contract: selection)
            pointer = nil
        case .action(let name, let description):
            guard command.kind == "native_action", offer.delivery == .objectAction, let node = resolved.binding.node
            else { throw DriverError("click action contract mismatch", code: .invalidRequest) }
            _ = try NativeObjectIntent(unit: command.kind, kind: "click", parameters: op.request.parameters)
            target = .action(node: node, name: name, description: description)
            pointer = nil
        case .menuAction(let name, let description):
            guard command.kind == "native_action", offer.delivery == .objectAction, let node = resolved.binding.node
            else { throw DriverError("click menu contract mismatch", code: .invalidRequest) }
            _ = try NativeObjectIntent(unit: command.kind, kind: "click", parameters: op.request.parameters)
            target = .menu(
                try access.units.menuUnit(command.target, within: contract.within), node: node, name: name,
                description: description)
            pointer = nil
        case .pointer(.click):
            guard command.kind == "pointer_sequence", [.windowDirected, .systemPointer].contains(offer.delivery) else {
                throw DriverError("click pointer contract mismatch", code: .invalidRequest)
            }
            let capture = command.target["capture"] is String
            let request = try NativePointerIntent(
                unit: command.kind, kind: "click", parameters: op.request.parameters, foreground: false,
                element: !capture)
            pointer = request
            if capture {
                let unit = NativePointerUnit(capture: try access.captures.input(command.target).entry)
                try unit.validateParameters(request)
                target = .captured(unit)
            } else {
                target = .element(try access.units.elementPointerUnit(command.target, actuator: contract.actuator))
            }
        case .desktop("click"):
            guard command.kind == "pointer_sequence", offer.delivery == .systemPointer, !command.foreground else {
                throw DriverError("desktop click contract mismatch", code: .invalidRequest)
            }
            pointer = try NativePointerIntent(
                unit: command.kind, kind: "click", parameters: op.request.parameters, foreground: false)
            target = .desktop(try access.units.desktopUnit(command.target))
        default: throw DriverError("selected primitive does not implement click", code: .invalidRequest)
        }
        guard !command.foreground || offer.preparationOptions.contains("foreground") else {
            throw DriverError("selected click offer does not permit foreground preparation", code: .invalidRequest)
        }
        guard offer.admission != .unavailable && offer.admission != .insufficientEvidence else {
            throw DriverError(offer.reason ?? "selected click route is unavailable", code: .inputUnavailable)
        }
        var plan = NativeClickPlan(
            target: target, offer: offer, pointer: pointer, command: command, operation: op, within: contract.within,
            step: step)
        if case .action(let node, let name, _) = target, name == kAXPressAction {
            plan.document = try access.objects.nativeActionFacts(node).document
            plan.documentPreparationRequired = try plan.document?.focus() == .other
        }
        return plan
    }
}
