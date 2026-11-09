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
        var unit = try context.access.units.keyboardUnit(
            bound, actuator: contract.actuator, receiver: contract.receiver)
        unit.originalAddressedElement = contract.node != nil
            && contract.node == op.target["node"] as? String && contract.actuator == nil
            && contract.receiver == nil && !(bound["capture"] is String)
        if let entry = contract.keyboardInsertion ?? replacement?.keyboardEntry {
            guard let element = unit.element, contract.actuator == nil, contract.receiver == nil,
                let node = contract.node, node == bound["node"] as? String,
                node == op.target["node"] as? String, !(bound["capture"] is String),
                NativeKeyboardTextEntry.read(element) == entry
            else { throw NativeRouteUnavailable(description: "original keyboard text-entry contract changed") }
            unit.keyboardInsertion = contract.keyboardInsertion
            unit.insertionSelection = op.keyboardInsertionSelection
        }
        if let provider = contract.geckoKeyboardInsertion {
            guard expected == .insert, provider == op.geckoTextProvider,
                unit.originalAddressedElement, unit.keyboardInsertion?.isPlainTextControl == true,
                let element = unit.element, CFEqual(element, provider.element), CFEqual(unit.window, provider.window),
                provider.pid == unit.application.processIdentifier
            else { throw NativeRouteUnavailable(description: "original process text insertion contract changed") }
            unit.geckoInsertion = provider
        }
        if let element = unit.element, AXAccess.read(element, kAXEnabledAttribute).value as? Bool == false {
            throw DriverError("the selected keyboard target is disabled", code: .inputUnavailable)
        }
        if expected == .replace, replacement?.keyboardEntry == nil {
            try checkReplacementContract(replacement) {
                unit.element.flatMap { NativeReplacementUnit(element: $0).contract() }
            }
        }
        let unconstrained = bound["node"] is NSNull && contract.within.isEmpty && contract.actuator == nil
            && contract.receiver == nil && unit.receiverRelation == nil
        func scopedBoundary(_ anchor: NativeKeyboardBoundaryAnchor?, phase: String) -> (
            check: @MainActor () throws -> Void, report: () -> Void
        ) {
            var policy = NativeKeyboardBoundaryPolicy(eligible: anchor != nil)
            let check: @MainActor () throws -> Void
            if let anchor {
                check = context.boundary.keyboardDispatchCheck {
                    try sampleKeyboardBoundary(
                        &policy, projection: { try context.access.scopes.keyboardBoundary(anchor) },
                        legacy: { try context.access.scopes.legacyKeyboardBoundary(anchor) })
                }
            } else {
                check = {
                    policy.recordLegacySample()
                    try checkBoundary()
                }
            }
            return (check, {
                diagnose(
                    ["kind": "keyboard_boundary_summary", "phase": phase, "eligible": anchor != nil,
                     "projectionHits": policy.projectionHits, "legacySamples": policy.legacySamples,
                     "firstNeedLegacy": policy.firstNeedLegacy as Any? ?? NSNull()] as [String: Any])
            })
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
                let anchor: NativeKeyboardBoundaryAnchor?
                if unconstrained, try unit.ordinaryKeyBoundaryEvidence(keyboard, textPolicy: textPolicy) {
                    anchor = try context.access.scopes.keyboardBoundaryAnchor(
                        bound, application: unit.application, window: unit.window)
                } else { anchor = nil }
                let boundary = scopedBoundary(anchor, phase: "preparation")
                defer { boundary.report() }
                try await unit.prepare(
                    keyboard, replacement: replacement, foreground: foreground, textPolicy: textPolicy,
                    check: boundary.check, onPreparation: receipt.recordPreparation,
                    onCleanupUnknown: receipt.recordCleanupUnknown)
                receipt.detail = "Keyboard receiver confirmed; no requested input was sent."
                receipt.confirmation = "confirmed"
            }
        }
        return .dispatch {
            var routeFacts: NativeKeyboardBoundaryRouteFacts?
            let delivery = try unit.selectedDelivery(keyboard, textPolicy: textPolicy, onSelection: { routeFacts = $0 })
            let anchor: NativeKeyboardBoundaryAnchor?
            if unconstrained, routeFacts?.localProcess == true, routeFacts?.localBoundaryEvidence == true,
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
                localBoundaryEvidence: routeFacts?.localBoundaryEvidence == true,
                sameKeyboardSurface: routeFacts?.sameKeyboardSurface == true)
            var dispatchUnit = unit
            let boundary = scopedBoundary(eligible ? anchor : nil, phase: "dispatch")
            defer { boundary.report() }
            if eligible, let anchor {
                guard routeFacts?.nativeID == anchor.nativeID else {
                    throw DriverError("original keyboard native window changed during selection", code: .staleTarget)
                }
                dispatchUnit.dispatchExpectedNativeID = anchor.nativeID
            }
            let checkSavePanelPath: (@MainActor (NativeSavePanelPathContinuation.Proof) throws -> Void)?
            if keyboard.kind == .insert, bound["node"] is String, contract.within.isEmpty,
                contract.actuator == nil, contract.receiver == nil
            {
                checkSavePanelPath = { proof in
                    try context.boundary.keyboardDispatchCheck {
                        try context.access.scopes.savePanelPathBoundary(bound, proof: proof)
                    }()
                }
            } else { checkSavePanelPath = nil }
            let alreadySatisfied = try await dispatchUnit.deliver(
                keyboard, delivery: delivery, replacement: replacement, prepared: op.preparationConfirmed(id),
                check: boundary.check,
                onDispatch: markDispatch, onPosted: receipt.recordAccepted, onPreparation: receipt.recordPreparation,
                onClipboardRestore: receipt.recordClipboardRestore, onCleanupUnknown: receipt.recordCleanupUnknown,
                allowsAttachedEditorHandoff: keyboard.kind == .insert && unconstrained && unit.windowCommandScope
                    && !(bound["capture"] is String),
                checkSavePanelPath: checkSavePanelPath)
            receipt.detail =
                alreadySatisfied
                ? "The complete target text is already empty; no replacement input was sent."
                : "\(intentKind) dispatched to the selected receiver."
            if alreadySatisfied { receipt.confirmation = "confirmed" }
            return alreadySatisfied
        }

    }

    static func checkReplacementContract(
        _ selected: NativeReplacementUnit.Selection?, sample: () -> NativeReplacementUnit.Selection?
    ) throws {
        guard let selected, sample() == selected.axContract else {
            throw DriverError("the selected replacement contract changed", code: .inputUnavailable)
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
            case .needsLegacy: break  // absorbing transition already recorded
            }
        }
        policy.recordLegacySample()
        return try legacy()
    }

}
