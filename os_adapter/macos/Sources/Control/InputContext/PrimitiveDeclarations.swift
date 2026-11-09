// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices

@MainActor final class NativePrimitiveDeclarations {
    let previous: [NativePrimitiveContract]
    let op: NativeOperation
    let binding: (wire: [String: Any], blocked: [String], node: String?)
    let within: [String]
    let actuator: NativeInspectQuery.Actuator?
    let receiver: NativeInspectQuery.Receiver?
    let basis: [NativeInspectQuery.Dependency]
    let selection: NativeSelectionBasis
    let scope: String
    private(set) var current = [NativePrimitiveContract]()
    private(set) var primitives = [[String: Any]]()

    init(
        previous: [NativePrimitiveContract], op: NativeOperation, query: NativeInputQueryScope, within: [String],
        actuator: NativeInspectQuery.Actuator?, receiver: NativeInspectQuery.Receiver?,
        basis: [NativeInspectQuery.Dependency]
    ) {
        self.previous = previous
        self.op = op
        self.binding = query.binding
        self.within = within
        self.actuator = actuator
        self.receiver = receiver
        self.basis = basis
        self.selection = query.selection
        self.scope = query.scope
    }

    /// RESOURCE/query evidence belongs to the original opaque contract,
    /// never a new global retry ledger or application-name capability cache.
    func keyboardPolicy(
        kind: NativeKeyboardIntent.Kind, selection replacement: NativeReplacementUnit.Selection?,
        observed: NativeKeyboardInterpreterEvidence.Kind
    ) -> NativeKeyboardInterpreterEvidence.Policy {
        var policy = NativeKeyboardInterpreterEvidence.Policy.existing
        for prior in previous
        where prior.node == binding.node && prior.within == within && prior.actuator == actuator
            && prior.receiver == receiver
        {
            if case .keyboard(let oldKind, _, let witness) = prior.unit, oldKind == kind {
                if witness == .localWebNoProcess {
                    policy = .localWebNoProcess
                    break
                }
                if witness == .webObserved { policy = .webObserved }
            }
        }
        return NativeKeyboardInterpreterEvidence.retain(policy, observed: observed)
    }

    func actionDocument(for unit: NativeInputUnit, facts: NativeElementControl.ActionFacts) throws
        -> NativeActionDocumentContract?
    {
        guard case .action(let action, _) = unit, action == kAXPressAction, let node = binding.node else { return nil }
        try op.check()
        let retained = op.actionDocuments.context(
            .init(node: node, surface: scope, within: within, actuator: actuator, receiver: receiver),
            observed: facts.document)
        try retained?.observe()
        return retained
    }

    /// A content node can locate a separate scrolling receiver. Its enabled
    /// state is still published, but does not grant or deny that receiver's
    /// permission. Aliases of the region/bar/button/hit keep their own gate.
    private func borrowsScrollPermission(_ unit: NativeInputUnit) -> Bool {
        guard let actuator, let node = binding.node, !actuator.references.contains(node) else { return false }
        switch unit {
        case .scroll: return actuator.relation == "scrollPage"
        case .pointer(.scroll): return actuator.relation == "scrollPointer"
        default: return false
        }
    }

    func declare(
        _ unit: NativeInputUnit, supported: Bool, enabled: Bool?, actionDocument: NativeActionDocumentContract? = nil,
        keyboardInsertion: NativeKeyboardTextEntry? = nil,
        geckoKeyboardInsertion: NativeGeckoTextProvider.Proof? = nil,
        readiness: (Bool) throws -> NativeReadiness
    ) rethrows {
        let prior = previous.first {
            $0.node == binding.node && $0.within == within && $0.actuator == actuator && $0.receiver == receiver
                && $0.unit == unit && $0.keyboardInsertion == keyboardInsertion
                && $0.geckoKeyboardInsertion == geckoKeyboardInsertion
                && selection.equivalent($0.basis, basis, scope: scope)
        }
        let contract = NativePrimitiveContract(
            id: prior?.id ?? UUID().uuidString, node: binding.node, within: within, actuator: actuator,
            receiver: receiver, basis: basis, unit: unit, actionDocument: actionDocument,
            keyboardInsertion: keyboardInsertion, geckoKeyboardInsertion: geckoKeyboardInsertion)
        current.append(contract)
        let sampled = try readiness(op.preparationConfirmed(contract.id))
        let conditions: NativeReadiness
        if case .menuAction(let name, _) = unit {
            if name == kAXCancelAction {
                conditions = NativeReadiness()
            } else if binding.wire["rootWindow"] is String, !op.preparationConfirmed(contract.id) {
                conditions = NativeReadiness([("receiver", "unknown")])
            } else {
                conditions = sampled
            }
        } else if case .action(let action, _) = unit, action == kAXPressAction,
            actionDocument?.selectionRequired == true, sampled.pending == ["receiver"]
        {
            // Selecting the known original window is independently necessary.
            // Its controls may become enabled only after that preparation.
            // Keep the raw enabled fact; refresh dispatch readiness afterwards.
            conditions = sampled
        } else if borrowsScrollPermission(unit) {
            // The selected scrolling unit checks its actual receiver permission
            // and original owner containment, including at final dispatch.
            conditions = sampled
        } else {
            conditions = sampled.requiringEnabled(enabled)
        }
        var wire = unit.declaration(id: contract.id, supported: supported, readiness: conditions)
        if case .keyboard(let kind, let replacement, _) = unit,
            keyboardInsertion != nil || replacement?.keyboardEntry != nil
        {
            wire["keyboardEditing"] = kind.rawValue
        }
        primitives.append(wire)
    }

    func declareClick(
        _ unit: NativeInputUnit, offer sampled: NativeClickContract, actionDocument: NativeActionDocumentContract? = nil
    ) {
        func sameProof(_ old: NativePopupPointerQualification?, _ new: NativePopupPointerQualification?) -> Bool {
            switch (old, new) {
            case (nil, nil): return true
            case (let a?, let b?): return a.matches(b, same: { CFEqual($0, $1) })
            default: return false
            }
        }
        let prior = previous.first {
            $0.node == binding.node && $0.within == within && $0.actuator == actuator && $0.receiver == receiver
                && $0.unit == unit && $0.click?.routeFamily == sampled.routeFamily
                && $0.click?.pointerRequirement == sampled.pointerRequirement
                && $0.click?.requiresForegroundGeometry == sampled.requiresForegroundGeometry
                && sameProof($0.click?.popupPointer, sampled.popupPointer)
                && ($0.click?.coordinateMenuPress == nil && sampled.coordinateMenuPress == nil
                    || $0.click?.coordinateMenuPress.flatMap { old in sampled.coordinateMenuPress.map(old.matches) } == true)
                && selection.equivalent($0.basis, basis, scope: scope)
        }
        let offer = sampled
        let contract = NativePrimitiveContract(
            id: prior?.id ?? UUID().uuidString, node: binding.node, within: within, actuator: actuator,
            receiver: receiver, basis: basis, unit: unit, click: offer, actionDocument: actionDocument)
        current.append(contract)
        let ready = offer.admission == .ready
        let supported = offer.admission == .ready || offer.admission == .preparationRequired
        let condition = offer.preparationOptions == ["foreground"] ? "foreground" : "receiver"
        var wire = unit.declaration(
            id: contract.id, supported: supported,
            readiness: ready ? NativeReadiness() : NativeReadiness([(condition, "unknown")]))
        wire["backgroundPreparation"] = offer.preparationOptions.contains("background") ? "yes" : "no"
        wire["click"] = offer.wire
        wire["globalInput"] = "no"
        primitives.append(wire)
    }

    /// Replace only this exact selection after collection is complete.
    func registeredContracts() throws -> [NativePrimitiveContract] {
        let other = previous.filter {
            $0.node != binding.node || $0.within != within || $0.actuator != actuator || $0.receiver != receiver
        }
        guard other.count + current.count <= 256 else {
            throw DriverError("native input contract budget exhausted", code: .observationIncomplete)
        }
        return other + current
    }
}
