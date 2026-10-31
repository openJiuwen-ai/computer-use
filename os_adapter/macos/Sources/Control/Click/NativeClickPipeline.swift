// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit

/// One admitted click attempt. Candidate selection and any later attempt stay
/// in the public Gate; this native boundary never changes the selected delivery.
@MainActor enum NativeClickPipeline {
    static func run(
        _ command: NativeUnitCommand, resolved: ResolvedNativeTarget, operation: NativeOperation, step: Int64,
        boundary: NativeUnitBoundary, receipt: NativeExecutionReceipt, access: NativeControlAccess
    ) async {
        let evidence = receipt.click!
        if let offer = resolved.contract.click { evidence.select(offer.routeFamily) }
        do {
            let plan = try ResolveNativeClickPlanPass.run(
                command, resolved: resolved, op: operation, step: step, access: access)
            try await NativeInputActivity.$stage.withValue(.preparation) {
                try await NativeClickPreparationPipeline.run(
                    plan, boundary: boundary, receipt: receipt, evidence: evidence)
            }
            try await DispatchNativeClickPass.run(
                plan, boundary: boundary, receipt: receipt, evidence: evidence, objects: access.objects)
        } catch {
            var failure = error
            if let observation = error as? NativeTogglePress.Unconfirmed {
                do {
                    if let result = try await ConfirmNativeToggleEffectPass.run(observation, boundary: boundary) {
                        receipt.dispatch = "dispatched"
                        receipt.detail = result.detail
                        receipt.confirmation = "confirmed"
                        evidence.complete()
                        return
                    }
                } catch { failure = error }
            }
            BuildNativeClickFailurePass.run(
                failure, command: command, operation: operation, step: step, offer: resolved.contract.click,
                within: resolved.contract.within, receipt: receipt, evidence: evidence)
        }
    }

    static func failResolution(
        _ error: Error, command: NativeUnitCommand, operation: NativeOperation, step: Int64,
        receipt: NativeExecutionReceipt
    ) {
        BuildNativeClickFailurePass.run(
            error, command: command, operation: operation, step: step, offer: nil, within: [], receipt: receipt,
            evidence: receipt.click!)
    }
}
