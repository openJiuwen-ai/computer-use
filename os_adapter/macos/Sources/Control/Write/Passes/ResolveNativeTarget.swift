// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices

/// QUERY/RESOURCE: resolve and retain the exact original target only after operation step admission.
@MainActor enum ResolveNativeTargetPass {
    static func run(_ command: NativeUnitCommand, op: NativeOperation, access: NativeControlAccess) throws
        -> ResolvedNativeTarget
    {
        let bound = command.target, id = command.primitiveID
        try access.sharing.checkAvailable()
        try op.check(write: true)
        // Resolve the live contract only after step deduplication.
        // A completed step remains retrievable if a later inspect
        // found different declarations or the action closed its UI.
        guard let contract = access.contracts.primitiveContracts(op.token).first(where: { $0.id == id }) else {
            throw DriverError("unknown native primitive", code: .invalidRequest)
        }
        let binding = try access.scopes.inputBinding(command.value["target"])
        guard contract.node == binding.node else {
            throw DriverError("native primitive belongs to a different target", code: .staleTarget)
        }
        guard binding.blocked.isEmpty else {
            throw DriverError(
                "target blocked by modal \(binding.blocked.joined(separator: ", "))", code: .blockedByModal)
        }
        try op.bindInputTarget(bound, within: contract.within) {
            var releases = [() -> Void]()
            for ref in contract.within + (binding.node.map { [$0] } ?? []) {
                guard let release = access.objects.retainNode(ref) else {
                    releases.reversed().forEach { $0() }
                    return nil
                }
                releases.append(release)
            }
            return { releases.reversed().forEach { $0() } }
        }
        access.scopes.aimPointer(bound, intent: op.request, receiver: contract.receiver?.reference)
        if let actuator = contract.actuator {
            for ref in Set(actuator.references) { try op.retainInputReference(ref) { access.objects.retainNode(ref) } }
        }
        if let receiver = contract.receiver {
            try op.retainInputReference(receiver.reference) { access.objects.retainNode(receiver.reference) }
        }
        return ResolvedNativeTarget(contract: contract, binding: binding)

    }
}
