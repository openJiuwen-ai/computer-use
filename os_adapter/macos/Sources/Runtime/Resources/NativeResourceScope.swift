// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices

/// Lifecycle service: resources are transferred once to NativeOperation, which
/// alone releases them. No new retry, worker or operation state is introduced.
@MainActor enum NativeResourceScope {
    static func begin(_ raw: Any?, timeoutMs: Int, access: NativeResourceAccess) async throws -> [String: Any] {
        let begin = try NativeBeginRequest(raw)
        guard !access.hasUnfinishedOperation() else {
            throw DriverError("another native operation owns the desktop", code: .invalidRequest)
        }
        let mode = begin.mode, request = begin.intent, intent = request.kind
        let budgetStart = ProcessInfo.processInfo.systemUptime
        let resolved = try ResolveNativeResourceTargetPass.run(begin, access: access)
        let releaseNode = resolved.releaseNode
        var admitted = false
        defer { if !admitted { releaseNode() } }
        let lease = mode == "write" ? try await DesktopLease.acquire() : nil
        do {
            let binding = try CheckNativeResourceBoundaryPass.run(resolved, mode: mode, access: access)
            // Arm before any preparation/input. The returned read suffix can
            // consume events from the write-to-observation gap without an RPC.
            let appChange = mode == "write" && !resolved.globalInput ? try access.directory.appChangeBaseline(binding) : nil
            let remaining = timeoutMs - Int((ProcessInfo.processInfo.systemUptime - budgetStart) * 1000)
            guard remaining > 0 else { throw RequestDeadlineExceeded() }
            let selection = NativeSelectionBasis(access: access.objects)
            let op = NativeOperation(
                timeoutMs: remaining, writable: mode == "write", intent: request, target: binding,
                clickContract: begin.clickContract,
                appChangeBaseline: appChange,
                sampleAppChange: { [directory = access.directory] in
                    guard let appChange else { return nil }
                    return directory.latestAppChange(appChange)
                },
                release: {
                    selection.clear()
                    releaseNode()
                    lease?.release()
                }, cancelActive: access.cancelActive)
            admitted = true
            access.register(op, selection, mode, intent)
            var result: [String: Any] = ["operation": op.token, "target": binding, "remainingMs": remaining]
            if let version = begin.clickContract { result["clickContract"] = version }
            if mode == "write" { result["appChangeBaseline"] = appChange?.wire as Any? ?? NSNull() }
            return result
        } catch {
            lease?.release()
            throw error
        }

    }
    static func finish(_ raw: Any?, operation: (Any?) throws -> NativeOperation) async throws -> [String: Any] {
        let p = try object(raw, keys: ["operation"])
        return ["cleanup": try operation(p["operation"]).finish()]
    }
}
