// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import Foundation
import CoreGraphics

/// Completion of the already-presented menu selection only. It does not
/// verify a business outcome, re-open a menu, or replay an input.
@MainActor enum CompleteNativeCoordinateMenuPressPass {
    /// After dispatch, AX children may disappear before AppKit delivers the
    /// selection callback. Observe the retained renderer, not input eligibility.
    /// An incomplete catalogue is unknown, never proof of dismissal.
    static func physicalPresentation(
        windowID: CGWindowID, pid: pid_t,
        query: () -> [[String: Any]]? = {
            CGWindowListCopyWindowInfo(.optionOnScreenOnly, kCGNullWindowID) as? [[String: Any]]
        }
    ) -> NativeForegroundResult.Presentation {
        guard let rows = query() else { return .unknown }
        var matched: [String: Any]?
        for row in rows {
            guard let id = (row[kCGWindowNumber as String] as? NSNumber)?.uint32Value else { return .unknown }
            if id == windowID {
                guard matched == nil else { return .unknown }
                matched = row
            }
        }
        guard let row = matched else { return .absent }
        guard (row[kCGWindowOwnerPID as String] as? NSNumber)?.int32Value == pid else { return .unknown }
        return (row[kCGWindowIsOnscreen as String] as? NSNumber)?.boolValue == false ? .absent : .present
    }

    static func run(
        windowID: CGWindowID, pid: pid_t,
        receipt: NativeExecutionReceipt, evidence: NativeClickEvidence,
        deadline: TimeInterval, check: () throws -> Void,
        query: () -> [[String: Any]]? = {
            CGWindowListCopyWindowInfo(.optionOnScreenOnly, kCGNullWindowID) as? [[String: Any]]
        },
        now: () -> TimeInterval = { ProcessInfo.processInfo.systemUptime },
        pause: (TimeInterval) async throws -> Void = { try await Task.sleep(for: .seconds($0)) }
    ) async throws {
        try await run(receipt: receipt, evidence: evidence, deadline: deadline, check: check,
                      presentation: { physicalPresentation(windowID: windowID, pid: pid, query: query) },
                      now: now, pause: pause)
    }

    static func run(
        receipt: NativeExecutionReceipt, evidence: NativeClickEvidence,
        deadline: TimeInterval, check: () throws -> Void,
        presentation: () throws -> NativeForegroundResult.Presentation,
        now: () -> TimeInterval = { ProcessInfo.processInfo.systemUptime },
        pause: (TimeInterval) async throws -> Void = { try await Task.sleep(for: .seconds($0)) }
    ) async throws {
        receipt.dispatch = "dispatched"
        evidence.complete()
        try check()
        var current = try presentation()
        let result = try await NativeMenuPresentationSettlement.wait(
            initiallyPresented: current == .present, deadline: deadline, check: check,
            presented: { current = try presentation(); return current == .present }, now: now, pause: pause)
        let state = current == .present ? "present" : current == .absent ? "absent" : "unknown"
        diagnose(["kind": "coordinate_menu_selection_settle", "samples": result.samples,
                  "durationMs": result.elapsed * 1000, "presentation": state])
    }
}
