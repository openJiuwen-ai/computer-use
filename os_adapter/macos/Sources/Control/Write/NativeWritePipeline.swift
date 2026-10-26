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
        return try await op.execute(step: request.step, payload: payload) {
            let receipt = NativeExecutionReceipt(target: decoded.target, onEffect: access.recordEffect)
            if op.clickContract != nil {
                // Lookup is inside dedup admission but before live resolution,
                // so an expired target still reports the selected stable family.
                let offer = access.contracts.primitiveContracts(op.token).first { $0.id == id }?.click
                receipt.click = NativeClickEvidence(
                    target: decoded.target, family: offer?.routeFamily ?? "macos.unresolved")
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
                        decoded, op: op, resolved: resolved, boundary: boundary, receipt: receipt, access: access)
                    switch route {
                    case .prepare(let body):
                        try await NativePreparePipeline.run(body, boundary: boundary, op: op, id: id)
                    case .dispatch(let body): try await NativeDispatchPipeline.run(body, receipt: receipt)
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
            return receipt.wire
        }
    }
}
