// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import ApplicationServices
import Foundation

/// Only injected snapshots/recording ports; no OS input or clipboard writes.
@main struct NativeInputActivityTests {
    @MainActor static func main() throws {
        var foreground: Int32? = 1
        var snapshots = 0
        var log = [[String: Any]]()
        let activity = NativeInputActivity(
            operation: "test", step: 1, command: "key_sequence", intent: "insert", target: [:],
            globalInputAllowed: true, clock: { 1 },
            snapshot: {
                snapshots += 1
                return ["foregroundPID": foreground as Any? ?? NSNull()]
            }, emit: { log.append($0) })
        for _ in 0..<500 {
            activity.begin(device: .keyboard, transport: .process, mechanism: "postToPid")?.returned()
        }
        let activation = activity.begin(device: .focus, transport: .activation, mechanism: "activate", stage: .preparation)
        foreground = 2
        activation?.returned(accepted: false)
        foreground = 1
        activity.begin(device: .keyboard, transport: .globalHID, mechanism: "hid")?.returned(nativeInvoked: false)
        _ = activity.begin(device: .mouse, transport: .globalHID, mechanism: "pendingHid")
        activity.begin(device: .clipboard, transport: .clipboard, mechanism: "clear", stage: .preparation)?.returned()
        activity.begin(device: .clipboard, transport: .clipboard, mechanism: "restore", stage: .cleanup)?.returned()
        activity.recordClipboardRestoration(.ownershipLost)
        let impact = activity.finish(dispatch: "unknown", cleanup: "unknown")
        let entries = impact["effects"] as! [[String: String]]
        precondition(entries.count == 6, "repeated keys must be grouped by actual invocation evidence")
        precondition(entries.contains { $0["mechanism"] == "postToPid" && $0["state"] == "invoked" })
        precondition(entries.contains { $0["mechanism"] == "activate" && $0["state"] == "invoked" },
                     "native accepted=false does not undo the fact the API was invoked")
        precondition(entries.contains { $0["mechanism"] == "hid" && $0["state"] == "notInvoked" })
        precondition(entries.contains { $0["mechanism"] == "pendingHid" && $0["state"] == "possible" })
        precondition(entries.contains { $0["mechanism"] == "restore" && $0["stage"] == "cleanup" })
        precondition(impact["coverage"] as? String == "complete")
        precondition(impact["foregroundChanged"] as? String == "yes", "transient focus change must survive returning to original")
        precondition(impact["clipboardRestoration"] as? String == "ownershipLost")
        let before = snapshots
        precondition(activity.finish(dispatch: "dispatched", cleanup: "complete") as NSDictionary == impact as NSDictionary)
        precondition(snapshots == before && log.last?["phase"] as? String == "end")
        precondition(log.last?["foregroundChanged"] as? String == "yes",
                     "end log must retain transient foreground change with the same meaning as the receipt")
        precondition(log.last?["foregroundNetChanged"] as? String == "no",
                     "returning to the initial app has no net change but still changed foreground in between")
        let receipt = NativeExecutionReceipt(target: [:], onEffect: {})
        receipt.dispatch = "partial"
        receipt.impact = impact
        precondition((receipt.wire["impact"] as? NSDictionary) == impact as NSDictionary)
        precondition(receipt.wire["dispatch"] as? String == "partial", "impact must not overwrite execution progress")

        var unknownLog = [[String: Any]]()
        let unknown = NativeInputActivity(
            operation: "unknown", step: 1, command: "native_action", intent: "action", target: [:],
            globalInputAllowed: false, snapshot: { [:] }, emit: { unknownLog.append($0) })
        let unavailable = unknown.finish(dispatch: "notDispatched", cleanup: "complete")
        precondition(unavailable["foregroundChanged"] as? String == "unknown")
        precondition(unavailable["coverage"] as? String == "complete")
        precondition(unavailable["clipboardRestoration"] as? String == "notUsed")
        precondition(unavailable["foregroundRestoration"] as? String == "notUsed")
        precondition(unknownLog.last?["foregroundNetChanged"] as? String == "unknown",
                     "missing PID evidence cannot prove no net foreground change")

        let many = NativeInputActivity(
            operation: "many", step: 1, command: "native_action", intent: "action", target: [:],
            globalInputAllowed: true, snapshot: { ["foregroundPID": Int32(1)] }, emit: { _ in })
        for index in 0..<1000 {
            many.begin(device: .element, transport: .accessibility, mechanism: "attribute\(index)")?.returned()
        }
        _ = many.begin(device: .keyboard, transport: .globalHID, mechanism: "pending global keyboard")
        let compact = many.finish(dispatch: "unknown", cleanup: "unknown")
        let grouped = compact["effects"] as! [[String: String]]
        precondition(grouped.count <= 128)
        precondition(grouped.contains { $0["transport"] == "globalHID" && $0["state"] == "possible" })
        precondition(compact["omittedEffects"] as? Int == 0)
        precondition(compact["coverage"] as? String == "partial")
        try providerInitialization()
        foregroundRestorationEvidence()
        keyboardEventDetails()
        print("PASS grouped invoked/possible/notInvoked effects, cleanup, clipboard ownership, foreground uncertainty, bounded impact")
    }

    @MainActor static func keyboardEventDetails() {
        let enabled = ProcessInfo.processInfo.environment["COMPUTER_USE_DIAGNOSE_KEY_EVENTS"] == "1"
        var log = [[String: Any]](), snapshots = 0
        let activity = NativeInputActivity(
            operation: "command", step: 1, command: "key_sequence", intent: "key", target: [:],
            globalInputAllowed: false, snapshot: { snapshots += 1; return ["foregroundPID": Int32(1)] },
            emit: { log.append($0) })
        NativeInputActivity.$current.withValue(activity) {
            for down in [true, false] {
                let event = CGEvent(keyboardEventSource: nil, virtualKey: 31, keyDown: down)!
                event.flags = .maskCommand
                NativeInputActivity.begin(
                    device: .keyboard, transport: .process, mechanism: "postToPid", pid: 1, event: event
                )?.returned()
            }
        }
        let impact = activity.finish(dispatch: "dispatched", cleanup: "complete")
        let events = log.filter { $0["phase"] as? String == "willCall" || $0["phase"] as? String == "didReturn" }
        precondition(events.count == 4 && snapshots == 6)
        precondition(events.last?["eventRole"] as? String == "release")
        for event in events {
            precondition((event["keycode"] as? Int64) == (enabled ? 31 : nil))
            precondition((event["flags"] as? UInt64) == (enabled ? CGEventFlags.maskCommand.rawValue : nil))
        }
        precondition((impact["effects"] as? [[String: String]])?.allSatisfy { $0["keycode"] == nil } == true)
    }

    @MainActor static func foregroundRestorationEvidence() {
        var log = [[String: Any]]()
        var reads = 0
        let activity = NativeInputActivity(
            operation: "restore", step: 1, command: "native_action", intent: "click", target: [:],
            globalInputAllowed: false,
            snapshot: { reads += 1; return ["foregroundPID": Int32(1)] }, emit: { log.append($0) })
        let before = reads
        // Both boundary snapshots see app 1. The preparation owner observed
        // app 2 becoming foreground asynchronously in between those snapshots.
        activity.recordForegroundObservation(2)
        activity.recordForegroundRestoration("restored", originalPID: 1, targetPID: 2)
        activity.recordForegroundRestoration("ownershipLost", originalPID: 1, targetPID: 3)
        activity.recordForegroundRestoration("notNeeded", originalPID: 1, targetPID: 4)
        precondition(reads == before, "recording a restoration must not query the desktop")
        let receipt = NativeExecutionReceipt(target: [:], onEffect: {})
        receipt.dispatch = "dispatched"
        receipt.impact = activity.finish(dispatch: receipt.dispatch, cleanup: receipt.cleanup)
        precondition(receipt.succeeded && receipt.dispatch == "dispatched" && receipt.cleanup == "complete",
                     "preserving a new foreground owner is not failed input or failed cleanup")
        precondition(receipt.impact?["foregroundChanged"] as? String == "yes",
                     "returning to the original app must not erase an observed transient activation")
        precondition(receipt.impact?["foregroundRestoration"] as? String == "ownershipLost",
                     "a later no-op preparation must not hide the earlier restoration outcome")
        let outcomes = log.filter { $0["phase"] as? String == "foregroundRestoration" }
        precondition(outcomes.count == 3 && outcomes[0]["originalPID"] as? Int32 == 1)
        precondition(outcomes[1]["outcome"] as? String == "ownershipLost")
    }

    @MainActor static func providerInitialization() throws {
        let process = NativeProcessIdentity.read(getpid())!
        let activation = AccessibilityActivation()
        let activity = NativeInputActivity(
            operation: "provider", step: 1, command: "native_action", intent: "action", target: [:],
            globalInputAllowed: false, snapshot: { ["foregroundPID": Int32(1)] }, emit: { _ in })
        var writes = 0
        NativeInputActivity.$current.withValue(activity) {
            for index in 0..<2 {
                let result = activation.request(
                    process, isCurrent: { true }, readEnabled: { _ in false },
                    writeEnabled: { _ in writes += 1; return .cannotComplete })
                precondition(result.kind == (index == 0 ? .requestReturned : .alreadyAttempted))
            }
        }
        let impact = activity.finish(dispatch: "notDispatched", cleanup: "complete")
        let effects = impact["effects"] as! [[String: String]]
        precondition(writes == 1 && effects.count == 1)
        precondition(effects[0]["mechanism"] == "AXManualAccessibility")
        precondition(effects[0]["stage"] == "preparation" && effects[0]["state"] == "invoked")
    }
}
