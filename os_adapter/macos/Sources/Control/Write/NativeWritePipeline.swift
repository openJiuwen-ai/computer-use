// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices

@MainActor enum NativeWritePipeline {
    static func execute(
        _ raw: Any?, operation: (Any?) throws -> NativeOperation, access: NativeControlAccess,
        launch: NativeLaunchAccess
    ) async throws -> [String: Any] {
        let request = try ResolveNativeWriteRequestPass.run(raw, operation: operation)
        let op = request.operation, decoded = request.command
        let kind = decoded.kind, foreground = decoded.foreground
        if kind == "launch_application" {
            return try await NativeLaunchPipeline.execute(op, step: request.step, command: decoded, access: launch)
        }
        guard !foreground || kind == "prepare_receiver" || op.clickContract != nil else {
            throw DriverError("only receiver preparation may request the foreground", code: .invalidRequest)
        }
        let id = decoded.primitiveID
        let payload = try JSONSerialization.data(
            withJSONObject: decoded.value, options: [.sortedKeys, .withoutEscapingSlashes])
        return try await op.execute(step: request.step, payload: payload, candidateID: id) {
            let receipt = NativeExecutionReceipt(target: decoded.target, onEffect: access.recordEffect)
            let activity = NativeInputActivity(
                operation: op.token, step: request.step, command: kind, intent: op.request.kind, target: decoded.target,
                globalInputAllowed: NativeInputAuthorization.globalInputAllowed)
            return await NativeInputActivity.$current.withValue(activity) {
                await NativeInputActivity.$stage.withValue(kind == "prepare_receiver" ? .preparation : .input) {
                    if op.clickContract != nil {
                        // Lookup is inside dedup admission but before live resolution,
                        // so an expired target still reports the selected stable family.
                        let offer = access.contracts.primitiveContracts(op.token).first { $0.id == id }?.click
                        receipt.click = NativeClickEvidence(
                            target: decoded.target, family: offer?.routeFamily ?? "macos.unresolved")
                    }
                    if kind == "global_input" {
                        do { try await NativeGlobalInputUnit.run(op, receipt: receipt, onEffect: access.recordEffect) }
                        catch { receipt.recordFailure(error) }
                        AgentPointer.current?.recordOutcome(success: receipt.succeeded)
                        receipt.impact = activity.finish(dispatch: receipt.dispatch, cleanup: receipt.cleanup)
                        return receipt.wire
                    }
                    do {
                        let resolved = try ResolveNativeTargetPass.run(decoded, op: op, access: access)
                        let boundary = NativeUnitBoundary.make(
                            decoded, op: op, resolved: resolved, receipt: receipt, access: access)
                        try CheckNativeWriteBoundaryPass.run(boundary)
                        if op.clickContract != nil {
                            await NativeClickPipeline.run(
                                decoded, resolved: resolved, operation: op, step: request.step, boundary: boundary,
                                receipt: receipt, access: access)
                        } else {
                            let route = try NativeRoutePipeline.select(
                                decoded, op: op, resolved: resolved, boundary: boundary, receipt: receipt,
                                access: access)
                            switch route {
                            case .prepare(let body):
                                try await NativePreparePipeline.run(body, boundary: boundary, op: op, id: id)
                            case .dispatch(let body):
                                if NativeForegroundResponsibility.current?.hasForegroundObligation == true,
                                    let source = access.scopes.retainedForegroundSource(decoded.target) {
                                    try await NativeForegroundRestorationScope.perform(
                                        target: BackgroundTarget(source.application), onPreparation: receipt.recordPreparation,
                                        onCleanupUnknown: receipt.recordCleanupUnknown, afterInput: true,
                                        inputState: {
                                            receipt.dispatch == "notDispatched" ? .notDispatched
                                                : receipt.dispatch == "dispatched" ? .completed : .uncertain
                                        }, presentation: {
                                            try NativeForegroundResult.presentation(
                                                source.element, pid: source.application.processIdentifier)
                                        }) {
                                            NativeForegroundRestorationScope.current?.willDispatchInput()
                                            try await NativeDispatchPipeline.run(body, receipt: receipt)
                                        }
                                } else { try await NativeDispatchPipeline.run(body, receipt: receipt) }
                            }
                        }
                    } catch {
                        if op.clickContract != nil {
                            NativeClickPipeline.failResolution(
                                error, command: decoded, operation: op, step: request.step, receipt: receipt)
                        } else {
                            BuildNativeFailureReceiptPass.run(
                                error, kind: kind, foreground: foreground, id: id, op: op, receipt: receipt)
                        }
                    }
                    AgentPointer.current?.recordOutcome(success: receipt.succeeded)
                    NativeForegroundResponsibility.current?.report(to: activity)
                    receipt.impact = activity.finish(dispatch: receipt.dispatch, cleanup: receipt.cleanup)
                    return receipt.wire
                }
            }
        }
    }
}
