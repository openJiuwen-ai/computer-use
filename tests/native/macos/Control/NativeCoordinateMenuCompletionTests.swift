// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import Foundation
import CoreGraphics

@main enum NativeCoordinateMenuCompletionTests {
    @MainActor static func main() async throws {
        var time: TimeInterval = 10, selected = "Alpha", reads = 0, sleeps = 0
        let receipt = NativeExecutionReceipt(target: [:], onEffect: {})
        let evidence = NativeClickEvidence(target: [:], family: NativeCoordinateMenuPress.family)
        evidence.markCall("nativeAction"); evidence.markReturned()
        // AX visible children are already empty as dismissal starts, while the
        // retained physical popup remains onscreen until the callback at350ms.
        // Exercise the production physical adapter, not an AX eligibility mock.
        let popup: [String: Any] = [kCGWindowNumber as String: 77, kCGWindowOwnerPID as String: 10]
        func physical(_ rows: [[String: Any]]?) -> NativeForegroundResult.Presentation {
            CompleteNativeCoordinateMenuPressPass.physicalPresentation(windowID: 77, pid: 10, query: { rows })
        }
        precondition(physical([popup]) == .present)
        precondition(physical([]) == .absent)
        precondition(physical(nil) == .unknown)
        precondition(physical([[:]]) == .unknown)
        precondition(physical([popup, popup]) == .unknown)
        precondition(physical([[kCGWindowNumber as String: 77, kCGWindowOwnerPID as String: 11]]) == .unknown)
        precondition(physical([[kCGWindowNumber as String: 77, kCGWindowOwnerPID as String: 10,
                                kCGWindowIsOnscreen as String: false]]) == .absent)
        try await CompleteNativeCoordinateMenuPressPass.run(
            windowID: 77, pid: 10, receipt: receipt, evidence: evidence, deadline: 20, check: {},
            query: {
                reads += 1
                if time >= 10.35 { selected = "Beta"; return [] }
                return [popup]
            }, now: { time }, pause: { time += $0; sleeps += 1 })
        precondition(selected == "Beta", "post-write observation ran before the delayed menu selection became observable")
        precondition(receipt.dispatch == "dispatched" && !evidence.permitsReselection)
        precondition(time >= 10.35 && time <= 10.5 && sleeps > 0 && reads > 1)

        let kept = NativeExecutionReceipt(target: [:], onEffect: {})
        time = 30
        try await CompleteNativeCoordinateMenuPressPass.run(
            receipt: kept, evidence: NativeClickEvidence(target: [:]), deadline: 40, check: {},
            presentation: { .present }, now: { time }, pause: { time += $0 })
        precondition(time == 30.5 && kept.dispatch == "dispatched" && kept.errorCode == nil,
                     "a menu intentionally kept open is not a failed selection and must not wait forever")

        time = 50
        try await CompleteNativeCoordinateMenuPressPass.run(
            receipt: kept, evidence: NativeClickEvidence(target: [:]), deadline: 50.12, check: {},
            presentation: { .present }, now: { time }, pause: { time += $0 })
        precondition(time <= 50.12, "settlement shares the original request deadline")

        time = 60
        try await CompleteNativeCoordinateMenuPressPass.run(
            receipt: kept, evidence: NativeClickEvidence(target: [:]), deadline: 70, check: {},
            presentation: { .unknown }, now: { time }, pause: { time += $0 })
        precondition(time == 60, "unknown presentation does not justify waiting or treating input as failed")

        time = 80
        do {
            try await CompleteNativeCoordinateMenuPressPass.run(
                receipt: kept, evidence: NativeClickEvidence(target: [:]), deadline: 90,
                check: { if time > 80 { throw CancellationError() } },
                presentation: { .present }, now: { time }, pause: { time += $0 })
            preconditionFailure("cancel must stop the completion wait")
        } catch is CancellationError {
            precondition(kept.dispatch == "dispatched", "cancel after AX success cannot erase input progress")
        }
        print("PASS background coordinate menu completion waits for natural animation; bounded/unknown/keep-open/cancel preserve input")
    }
}
