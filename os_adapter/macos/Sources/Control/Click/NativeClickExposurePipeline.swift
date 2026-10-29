// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit

/// Bounded preparation of the original target, within the selected delivery.
/// Scrolls are preparation effects; they never reset or replay the click.
@MainActor enum NativeClickExposurePipeline {
    static func run(
        _ plan: NativeClickPlan, boundary: NativeUnitBoundary, receipt: NativeExecutionReceipt,
        evidence: NativeClickEvidence
    ) async throws {
        guard case .element(let unit) = plan.target else { return }
        let deadline = min(RequestDiagnostics.deadline ?? .infinity, ProcessInfo.processInfo.systemUptime + 3)
        var steps = 0
        while let exposure = try unit.exposure() {
            try boundary.checkConditions()
            guard steps < 8, ProcessInfo.processInfo.systemUptime < deadline else {
                throw DriverError(
                    "the original element could not be scrolled into view within the click budget",
                    code: .inputUnavailable)
            }
            try await ScrollNativeClickTargetPass.run(
                exposure, unit: unit, delivery: plan.offer.delivery, deadline: deadline,
                boundary: boundary, receipt: receipt, evidence: evidence)
            steps += 1
        }
    }
}
