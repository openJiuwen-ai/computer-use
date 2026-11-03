// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

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
        let receipt = NativeExecutionReceipt(target: [:], onEffect: {})
        receipt.dispatch = "partial"
        receipt.impact = impact
        precondition((receipt.wire["impact"] as? NSDictionary) == impact as NSDictionary)
        precondition(receipt.wire["dispatch"] as? String == "partial", "impact must not overwrite execution progress")

        let unknown = NativeInputActivity(
            operation: "unknown", step: 1, command: "native_action", intent: "action", target: [:],
            globalInputAllowed: false, snapshot: { [:] }, emit: { _ in })
        let unavailable = unknown.finish(dispatch: "notDispatched", cleanup: "complete")
        precondition(unavailable["foregroundChanged"] as? String == "unknown")
        precondition(unavailable["coverage"] as? String == "complete")
        precondition(unavailable["clipboardRestoration"] as? String == "notUsed")

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
        print("PASS grouped invoked/possible/notInvoked effects, cleanup, clipboard ownership, foreground uncertainty, bounded impact")
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
