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
        if op.requiresRevealedLeafHit(command.target, within: contract.within) {
            guard case .pointer(.click) = contract.unit, resolved.binding.node != nil,
                contract.actuator == nil, contract.receiver == nil, !command.foreground,
                NativeClickClippedLeaf.permitsContinuation(offer)
            else {
                throw DriverError("the revealed element requires a ready exact click", code: .inputUnavailable)
            }
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
            if let press = offer.coordinateMenuPress {
                guard command.kind == "native_action", offer.delivery == .objectAction,
                    offer.routeFamily == NativeCoordinateMenuPress.family, name == kAXPressAction,
                    command.target["capture"] as? String == press.capture, !command.foreground,
                    NativeCoordinateMenuPress.permits(op.request)
                else { throw DriverError("coordinate menu Press contract mismatch", code: .invalidRequest) }
                target = .coordinateMenuPress(press)
                pointer = nil
                break
            }
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
            guard command.kind == "pointer_sequence", offer.delivery == .windowDirected else {
                throw DriverError("click pointer contract mismatch", code: .invalidRequest)
            }
            let capture = command.target["capture"] is String
            let request = try NativePointerIntent(
                unit: command.kind, kind: "click", parameters: op.request.parameters, foreground: false,
                element: !capture)
            pointer = request
            if offer.routeFamily == NativeMenuCommandPolicy.pointerFamily {
                guard !capture, contract.actuator == nil, contract.receiver == nil,
                    NativePopupClickPolicy.permitsIntent(op.request)
                else { throw DriverError("menu mouse contract mismatch", code: .invalidRequest) }
                target = .menuPointer(try access.units.menuUnit(command.target, within: contract.within))
            } else if let qualification = offer.popupPointer {
                guard !capture, contract.actuator == nil,
                    offer.delivery == .windowDirected, !command.foreground,
                    NativePopupClickPolicy.permitsIntent(op.request)
                else { throw DriverError("popup point contract mismatch", code: .invalidRequest) }
                target = .popupPointer(try access.units.popupPointerUnit(command.target, qualification: qualification))
            } else if capture {
                guard !offer.requiresForegroundGeometry, !offer.requiresExactElementHit else {
                    throw DriverError("retained-element geometry cannot authorize a captured point", code: .invalidRequest)
                }
                guard offer.pointerRequirement == .contentReceiver || offer.delivery == .windowDirected else {
                    throw DriverError("captured point requirement needs window-directed delivery", code: .invalidRequest)
                }
                let unit = NativePointerUnit(
                    capture: try access.captures.input(command.target), pointerRequirement: offer.pointerRequirement)
                try unit.validateParameters(request)
                target = .captured(unit)
            } else {
                guard offer.permitsElementPointer(hasActuator: contract.actuator != nil) else {
                    throw DriverError("original element point requirement needs its window-directed route", code: .invalidRequest)
                }
                let unit = try access.units.elementPointerUnit(command.target, actuator: contract.actuator)
                    .requiring(
                        offer.pointerRequirement, foregroundGeometry: offer.requiresForegroundGeometry,
                        exactElementHit: offer.requiresExactElementHit)
                if let original = offer.qtComboChoice {
                    guard let current = unit.qtComboChoice, original.matches(current) else {
                        throw DriverError("the selected Qt popup choice changed before dispatch", code: .staleTarget)
                    }
                }
                target = .element(unit)
            }
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
        if case .action(let node, let name, _) = target, name == kAXPressAction || name == kAXShowMenuAction {
            let facts = try access.objects.nativeActionFacts(node).resolvingDocument {
                try access.scopes.fixedInputWindow(command.target)
            }
            plan.qtPopupOpener = facts.qtPopupOpener.flatMap { $0.applies(action: name) ? $0 : nil }
            if name == kAXPressAction {
                let documentScope = NativeActionDocumentContexts.Scope(
                    node: node, surface: try string(resolved.binding.wire["surface"], label: "surface"),
                    within: contract.within, actuator: contract.actuator, receiver: contract.receiver)
                plan.documentScope = documentScope
                plan.actionDocument =
                    contract.actionDocument ?? op.actionDocuments.context(documentScope, observed: facts.document)
                try plan.actionDocument?.observe()
                plan.document = plan.actionDocument?.unit ?? facts.document
                plan.documentPreparation =
                    try plan.actionDocument.map { .init(windowSelectionRequired: $0.selectionRequired) }
                    ?? facts.documentPreparation(name)
            }
            if plan.documentPreparation.windowSelectionRequired || plan.actionDocument?.selectionRequired == true
                || plan.qtPopupOpener != nil
            {
                plan.actionContext = try access.units.actionContextUnit(command.target, action: name)
            }
        }
        return plan
    }
}
