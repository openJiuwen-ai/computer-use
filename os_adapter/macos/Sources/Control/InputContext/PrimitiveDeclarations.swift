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

    func declare(_ unit: NativeInputUnit, supported: Bool, enabled: Bool?, readiness: (Bool) throws -> NativeReadiness)
        rethrows
    {
        let prior = previous.first {
            $0.node == binding.node && $0.within == within && $0.actuator == actuator && $0.receiver == receiver
                && $0.unit == unit && selection.equivalent($0.basis, basis, scope: scope)
        }
        let contract = NativePrimitiveContract(
            id: prior?.id ?? UUID().uuidString, node: binding.node, within: within, actuator: actuator,
            receiver: receiver, basis: basis, unit: unit)
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
        } else {
            conditions = sampled.requiringEnabled(enabled)
        }
        primitives.append(
            unit.declaration(
                id: contract.id, supported: supported, readiness: conditions, exclusive: BackgroundTarget.isExclusive))
    }

    func declareClick(_ unit: NativeInputUnit, offer sampled: NativeClickContract) {
        let prior = previous.first {
            $0.node == binding.node && $0.within == within && $0.actuator == actuator && $0.receiver == receiver
                && $0.unit == unit && $0.click?.routeFamily == sampled.routeFamily
                && selection.equivalent($0.basis, basis, scope: scope)
        }
        let offer = sampled.retainingDocumentForeground(from: prior?.click)
        let contract = NativePrimitiveContract(
            id: prior?.id ?? UUID().uuidString, node: binding.node, within: within, actuator: actuator,
            receiver: receiver, basis: basis, unit: unit, click: offer)
        current.append(contract)
        let ready = offer.admission == .ready
        let supported = offer.admission == .ready || offer.admission == .preparationRequired
        let condition = offer.preparationOptions == ["foreground"] ? "foreground" : "receiver"
        var wire = unit.declaration(
            id: contract.id, supported: supported,
            readiness: ready ? NativeReadiness() : NativeReadiness([(condition, "unknown")]),
            exclusive: BackgroundTarget.isExclusive)
        wire["backgroundPreparation"] = offer.preparationOptions.contains("background") ? "yes" : "no"
        wire["click"] = offer.wire
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
