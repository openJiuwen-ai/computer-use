// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit

/// PREPARE: one wheel in the original element's known scroll region, followed
/// by bounded readback. No focus, key recipe, target substitution or click.
@MainActor enum ScrollNativeClickTargetPass {
    static func run(
        _ exposure: NativeElementViewport.Exposure, unit: NativeElementPointerUnit, delivery: NativeClickDelivery,
        deadline: TimeInterval, boundary: NativeUnitBoundary, receipt: NativeExecutionReceipt,
        evidence: NativeClickEvidence
    ) async throws {
        let pointer = unit.pointerUnit(mapping: exposure.mapping)
        let request = try NativePointerIntent(
            unit: "pointer_sequence", kind: "scroll",
            parameters: ["direction": exposure.direction, "pages": exposure.pages], foreground: false, element: true)
        var entered = false
        let detail = exposure.probe
            ? "probe passive window background to expose the original element"
            : "scroll original element into view"
        try await pointer.deliver(
            request, from: exposure.point, to: nil, viewport: exposure.viewport.size,
            check: {
                try boundary.checkConditions()
                if exposure.probe {
                    try NativeWindowScrollExposure.requirePassiveReceiver(
                        at: exposure.point, application: unit.application, window: unit.window, mapping: exposure.mapping)
                }
            },
            onDispatch: {
                if !entered {
                    entered = true
                    receipt.recordPreparation(detail)
                    evidence.record(
                        .init(kind: .viewport, phase: .willSend, detail: detail, window: exposure.mapping.id))
                }
            }, onPosted: {}, onCleanupUnknown: receipt.recordCleanupUnknown, delivery: delivery)
        evidence.record(.init(kind: .viewport, phase: .returned, detail: detail, window: exposure.mapping.id))
        let waitUntil = min(deadline, ProcessInfo.processInfo.systemUptime + 0.4)
        var frame = exposure.frame.offsetBy(dx: -exposure.mapping.frame.minX, dy: -exposure.mapping.frame.minY)
        var changedAt = ProcessInfo.processInfo.systemUptime
        repeat {
            // Waiting only samples the retained geometry. The boundary is
            // checked before every native event and again before the click.
            try RequestDiagnostics.checkCancellation()
            let sampled = try unit.viewport(), now = ProcessInfo.processInfo.systemUptime
            guard sampled.mapping.id == exposure.mapping.id, sampled.mapping.pid == exposure.mapping.pid else {
                throw DriverError("the scroll preparation's original window changed", code: .staleTarget)
            }
            let local = sampled.frame.offsetBy(dx: -sampled.mapping.frame.minX, dy: -sampled.mapping.frame.minY)
            if local != frame {
                frame = local
                changedAt = now
            }
            // A disappearing overlay bar / missing parent is not success. The
            // same original target must actually become visible or move closer.
            let progressed = NativeElementViewport.progressed(
                frame: sampled.frame, clipped: sampled.clipped, window: sampled.mapping.frame,
                from: exposure.frame, originalWindow: exposure.mapping.frame, viewport: exposure.viewport)
            if progressed && now - changedAt >= 0.06 {
                evidence.confirmViewportProgress(window: exposure.mapping.id)
                return
            }
            try await Task.sleep(for: .milliseconds(20))
        } while ProcessInfo.processInfo.systemUptime < waitUntil
        throw DriverError(
            "the original element's scroll region did not move toward the target", code: .inputUnavailable)
    }
}
