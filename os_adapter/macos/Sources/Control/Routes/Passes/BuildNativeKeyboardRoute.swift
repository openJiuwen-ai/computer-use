// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices

/// QUERY: construct only this selected native route; PREPARE/DISPATCH is deferred to its returned closure.
@MainActor enum BuildNativeKeyboardRoutePass {
    static func run(
        _ context: NativeRouteContext, expected: NativeKeyboardIntent.Kind,
        replacement: NativeReplacementUnit.Selection?, textPolicy: NativeKeyboardInterpreterEvidence.Policy
    ) throws -> SelectedNativeRoute {
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

        let keyboard = try NativeKeyboardIntent(
            unit: kind, kind: intentKind, parameters: op.request.parameters, foreground: foreground)
        guard keyboard.kind == expected else {
            throw DriverError("keyboard primitive intent changed", code: .invalidRequest)
        }
        let unit = try context.access.units.keyboardUnit(
            bound, actuator: contract.actuator, receiver: contract.receiver)
        if let element = unit.element, AXAccess.read(element, kAXEnabledAttribute).value as? Bool == false {
            throw DriverError("the selected keyboard target is disabled", code: .inputUnavailable)
        }
        if expected == .replace {
            guard let element = unit.element, let replacement,
                NativeReplacementUnit(element: element).contract() == replacement
            else { throw DriverError("the selected replacement contract changed", code: .inputUnavailable) }
        }
        if kind == "prepare_receiver" {
            return .prepare {
                if foreground {
                    // Fresh same-scope mechanism evidence, not a fabricated failed attempt.
                    if try unit.backgroundTextTransportUnavailable(keyboard, textPolicy: textPolicy) {
                        try op.recordBackgroundMechanismUnavailable(id)
                    }
                    try op.requireForegroundPreparation(id)
                }
                try await unit.prepare(
                    keyboard, replacement: replacement, foreground: foreground, textPolicy: textPolicy,
                    check: checkBoundary, onPreparation: receipt.recordPreparation,
                    onCleanupUnknown: receipt.recordCleanupUnknown)
                receipt.detail = "Keyboard receiver confirmed; no requested input was sent."
                receipt.confirmation = "confirmed"
            }
        }
        return .dispatch {
            var routeFacts: NativeKeyboardBoundaryRouteFacts?
            let delivery = try unit.selectedDelivery(keyboard, textPolicy: textPolicy, onSelection: { routeFacts = $0 })
            let unconstrained =
                bound["node"] is NSNull && contract.within.isEmpty && contract.actuator == nil
                && contract.receiver == nil && unit.receiverRelation == nil
            let anchor: NativeKeyboardBoundaryAnchor?
            if unconstrained, routeFacts?.localProcess == true, routeFacts?.noObservedWeb == true,
                routeFacts?.sameKeyboardSurface == true
            {
                anchor = try context.access.scopes.keyboardBoundaryAnchor(
                    bound, application: unit.application, window: unit.window)
            } else {
                anchor = nil
            }
            let eligible = NativeKeyboardBoundaryPolicy.eligible(
                windowCommand: unit.windowCommandScope, unconstrained: unconstrained, sameRoot: anchor != nil,
                exactNativeID: anchor != nil, localProcess: routeFacts?.localProcess == true,
                noObservedWeb: routeFacts?.noObservedWeb == true,
                sameKeyboardSurface: routeFacts?.sameKeyboardSurface == true)
            var dispatchUnit = unit
            var policy = NativeKeyboardBoundaryPolicy(eligible: eligible)
            defer {
                diagnose(
                    [
                        "kind": "keyboard_boundary_summary", "eligible": eligible,
                        "projectionHits": policy.projectionHits, "legacySamples": policy.legacySamples,
                        "firstNeedLegacy": policy.firstNeedLegacy as Any? ?? NSNull()
                    ] as [String: Any])
            }
            let dispatchCheck: @MainActor () throws -> Void
            if eligible, let anchor {
                guard routeFacts?.nativeID == anchor.nativeID else {
                    throw DriverError("original keyboard native window changed during selection", code: .staleTarget)
                }
                dispatchUnit.dispatchExpectedNativeID = anchor.nativeID
                dispatchCheck = context.boundary.keyboardDispatchCheck {
                    try sampleKeyboardBoundary(
                        &policy, projection: { try context.access.scopes.keyboardBoundary(anchor) },
                        legacy: { try context.access.scopes.legacyKeyboardBoundary(anchor) })
                }
            } else {
                dispatchCheck = {
                    policy.recordLegacySample()
                    try checkBoundary()
                }
            }
            if case .scopedForegroundHID(let scoped) = delivery {
                try await unit.confirmSelectedDelivery(delivery, check: checkBoundary)
                try NativeInputActivity.$stage.withValue(.preparation) {
                    try NativeScopedKeyboardPreparation.prepare(
                        scoped, check: checkBoundary, onPreparation: receipt.recordPreparation)
                }
            }
            let alreadySatisfied = try await dispatchUnit.deliver(
                keyboard, delivery: delivery, replacement: replacement, prepared: op.preparationConfirmed(id),
                check: dispatchCheck,
                prepareDispatch: { if delivery.isScopedHID { try context.boundary.checkKeyboardSelection() } },
                onDispatch: {
                    if delivery.isScopedHID {
                        try context.boundary.markPreparedKeyboardDispatchInFlight()
                    } else {
                        try markDispatch()
                    }
                }, onPosted: receipt.recordAccepted, onPreparation: receipt.recordPreparation,
                onClipboardRestore: receipt.recordClipboardRestore, onCleanupUnknown: receipt.recordCleanupUnknown)
            receipt.detail =
                alreadySatisfied
                ? "The complete target text is already empty; no replacement input was sent."
                : "\(intentKind) dispatched to the selected receiver; application effect unconfirmed."
            if alreadySatisfied { receipt.confirmation = "confirmed" }
            return alreadySatisfied
        }

    }
    /// Parent composition for this selected route. Ports remain explicit at
    /// the callsite; the policy helper has no AX/full-scope API or input effects.
    static func sampleKeyboardBoundary(
        _ policy: inout NativeKeyboardBoundaryPolicy, projection: () throws -> NativeKeyboardBoundaryPolicy.Decision,
        legacy: () throws -> [String]
    ) throws -> [String] {
        if policy.mode == .projection {
            let decision = try projection()
            policy.recordProjection(decision)
            switch decision {
            case .confirmed: return []
            case .changed: throw DriverError("original keyboard AX window closed", code: .staleTarget)
            case .needsLegacy: break  // absorbing transition already recorded
            }
        }
        policy.recordLegacySample()
        return try legacy()
    }

}
