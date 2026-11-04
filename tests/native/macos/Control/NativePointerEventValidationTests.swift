// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices

/// Exercise the production validation pass through the production paired
/// event sequence. Only query leaves and the OS post are replaced; no UI.
@main enum NativePointerEventValidationTests {
    @MainActor final class Scene {
        enum Fault {
            case none, operationCancelled, queryFailed, cancelDuringQuery, receiverChangedAfterHover,
                 readinessLostAfterHover, heldDuringFinalQuery, closeOnDown, closeBeforeSecondClick, processGoneOnDown
        }
        let fault: Fault
        let point = CGPoint(x: 25, y: 35)
        var posts = [CGEventType]()
        var operationChecks = 0, queries = 0, targetChecks = 0, physicalChecks = 0
        var releaseChecks = 0, skippedReleases = 0, marked = 0, returned = 0
        var targetExists = true, processExists = true, cancelled = false, held = false
        var stages = [String]()

        init(_ fault: Fault = .none) { self.fault = fault }

        func run() async throws {
            let request = try NativePointerIntent(
                unit: "pointer_sequence", kind: "click",
                parameters: ["x": 25, "y": 35, "count": fault == .closeBeforeSecondClick ? 2 : 1],
                foreground: false)
            let sequence = try NativePointerEvents(
                request: request, from: point, to: nil, viewport: CGSize(width: 100, height: 100),
                source: CGEventSource(stateID: .privateState)!)
            let entry = NSEvent.enterExitEvent(
                with: .mouseEntered, location: point, modifierFlags: [], timestamp: 0, windowNumber: 71,
                context: nil, eventNumber: 0, trackingNumber: 0, userData: nil)!.cgEvent!
            try await sequence.deliver(
                entry: entry,
                check: { point, _ in
                    precondition(point == self.point, "a later query cannot move the original requested point")
                    try ValidateNativePointerEventPass.run(
                        kind: .click, continuingDrag: false,
                        guardOperation: {
                            self.operationChecks += 1
                            self.stages.append("operation")
                            if self.fault == .operationCancelled { throw CancellationError() }
                        }, checkTarget: {
                            self.targetChecks += 1
                            self.stages.append("target")
                            if self.cancelled { throw CancellationError() }
                            guard self.targetExists else { throw DriverError("original target closed", code: .staleTarget) }
                            if self.fault == .receiverChangedAfterHover && self.posts.contains(.mouseMoved) {
                                throw DriverError("original receiver changed", code: .staleTarget)
                            }
                            // A real user can press a physical button while
                            // this final AX query is in flight.
                            if self.fault == .heldDuringFinalQuery && self.posts.contains(.mouseMoved) {
                                self.held = true
                            }
                        }, queryRecipient: {
                            self.queries += 1
                            self.stages.append("recipient")
                            if self.fault == .queryFailed { throw DriverError("query failed", code: .observationIncomplete) }
                            if self.fault == .cancelDuringQuery { self.cancelled = true }
                        }, queryReadiness: {
                            self.stages.append("readiness")
                            return self.fault != .readinessLostAfterHover || !self.posts.contains(.mouseMoved)
                        }, checkPhysicalInput: {
                            self.physicalChecks += 1
                            self.stages.append("physical")
                            try NativePhysicalInput.validate(.globalHID, sample: {
                                .init(modifiers: [], buttons: self.held ? [.left] : [])
                            })
                        }, unavailable: { DriverError("receiver not ready", code: .inputUnavailable) })
                }, post: { event, point in
                    precondition(point == self.point)
                    self.posts.append(event.type)
                    if event.type == .leftMouseDown {
                        if [.closeOnDown, .closeBeforeSecondClick].contains(self.fault) { self.targetExists = false }
                        if self.fault == .processGoneOnDown { self.processExists = false }
                    }
                }, canRelease: {
                    self.releaseChecks += 1
                    return self.processExists
                }, skipped: { self.skippedReleases += 1 },
                onDispatch: { self.marked += 1 }, onPosted: { self.returned += 1 })
        }
    }

    @MainActor static func main() async throws {
        let normal = Scene()
        try await normal.run()
        precondition(normal.posts.map(\.rawValue) == [8, 5, 1, 2])
        precondition(normal.operationChecks == 3 && normal.queries == 3 && normal.targetChecks == 3,
                     "each original non-release event must have exactly one final complete target query")
        precondition(normal.physicalChecks == 3 && normal.marked == 3 && normal.returned == 3)
        precondition(normal.stages == Array(repeating: ["operation", "recipient", "readiness", "target", "physical"],
                                             count: 3).flatMap { $0 })

        for fault in [Scene.Fault.operationCancelled, .queryFailed, .cancelDuringQuery] {
            let scene = Scene(fault)
            do { try await scene.run(); preconditionFailure("failed query/operation posted input") }
            catch is CancellationError { precondition(fault != .queryFailed) }
            catch let error as DriverError { precondition(fault == .queryFailed && error.code == .observationIncomplete) }
            precondition(scene.posts.isEmpty && scene.marked == 0 && scene.releaseChecks == 0)
        }
        for fault in [Scene.Fault.receiverChangedAfterHover, .readinessLostAfterHover, .heldDuringFinalQuery] {
            let scene = Scene(fault)
            do { try await scene.run(); preconditionFailure("changed receiver/held conflict received down") }
            catch let error as DriverError {
                precondition(error.code == (fault == .receiverChangedAfterHover ? .staleTarget : .inputUnavailable))
            }
            precondition(scene.posts.map(\.rawValue) == [8, 5], "hover cannot authorize a later changed down")
            precondition(scene.targetChecks == 3 && scene.marked == 2 && scene.releaseChecks == 0)
        }
        let closed = Scene(.closeOnDown)
        try await closed.run()
        precondition(!closed.targetExists && closed.posts.map(\.rawValue) == [8, 5, 1, 2],
                     "normal down effects cannot prevent paired up into the original live process")
        precondition(closed.targetChecks == 3 && closed.releaseChecks == 1)

        let second = Scene(.closeBeforeSecondClick)
        do { try await second.run(); preconditionFailure("a second down reused the first click's admission") }
        catch let error as DriverError { precondition(error.code == .staleTarget) }
        precondition(second.posts.map(\.rawValue) == [8, 5, 1, 2] && second.releaseChecks == 1)

        let dead = Scene(.processGoneOnDown)
        try await dead.run()
        precondition(dead.posts.map(\.rawValue) == [8, 5, 1] && dead.skippedReleases == 1,
                     "cleanup cannot release into a replacement/dead original process")
        try nonClickBoundariesRemainExplicit()
        print("Pointer event query/final-target/held ordering and original paired release passed")
    }

    @MainActor static func nonClickBoundariesRemainExplicit() throws {
        for kind in [NativePointerIntent.Kind.scroll, .drag] {
            var stages = [String]()
            try ValidateNativePointerEventPass.run(
                kind: kind, continuingDrag: kind == .drag,
                guardOperation: { preconditionFailure("click-only operation guard leaked into another mechanism") },
                checkTarget: { stages.append("target") }, queryRecipient: { stages.append("recipient") },
                queryReadiness: { stages.append("readiness"); return true },
                checkPhysicalInput: { stages.append("physical") },
                unavailable: { DriverError("unavailable", code: .inputUnavailable) })
            precondition(stages == (kind == .drag
                ? ["target", "recipient", "physical"]
                : ["target", "recipient", "physical", "readiness", "target"]))
        }
    }
}
