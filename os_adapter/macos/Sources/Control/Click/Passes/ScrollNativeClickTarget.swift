import AppKit

/// PREPARE: one wheel in the original element's known scroll region, followed
/// by bounded readback. No focus, key recipe, target substitution or click.
@MainActor enum ScrollNativeClickTargetPass {
    static func run(
        _ exposure: NativeElementViewport.Exposure, unit: NativeElementPointerUnit, delivery: NativeClickDelivery,
        deadline: TimeInterval, boundary: NativeUnitBoundary, receipt: NativeExecutionReceipt,
        evidence: NativeClickEvidence
    ) async throws {
        let pointer = NativePointerUnit(
            application: unit.application, window: exposure.mapping, nativeWindow: unit.window)
        let request = try NativePointerIntent(
            unit: "pointer_sequence", kind: "scroll",
            parameters: ["direction": exposure.direction, "pages": exposure.pages], foreground: false, element: true)
        var entered = false
        let detail = "scroll original element into view"
        try await pointer.deliver(
            request, from: exposure.point, to: nil, viewport: exposure.viewport.size,
            check: boundary.checkConditions,
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
        var frame = exposure.frame, changedAt = ProcessInfo.processInfo.systemUptime
        repeat {
            // Waiting only samples the retained geometry. The boundary is
            // checked before every native event and again before the click.
            try RequestDiagnostics.checkCancellation()
            let sampled = try unit.viewport(), now = ProcessInfo.processInfo.systemUptime
            if sampled.frame != frame {
                frame = sampled.frame
                changedAt = now
            }
            let progressed = sampled.exposure.map {
                !CFEqual($0.region, exposure.region) || $0.distance < exposure.distance - 0.5
            } ?? true
            if progressed && now - changedAt >= 0.06 { return }
            try await Task.sleep(for: .milliseconds(20))
        } while ProcessInfo.processInfo.systemUptime < waitUntil
        throw DriverError(
            "the original element's scroll region did not move toward the target", code: .inputUnavailable)
    }
}
