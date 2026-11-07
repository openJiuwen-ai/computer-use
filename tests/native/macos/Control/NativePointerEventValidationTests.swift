// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices

/// Exercise the production validation pass through the production paired
/// event sequence. Only query leaves and the OS post are replaced; no UI.
@main enum NativePointerEventValidationTests {
    @MainActor final class Scene {
        enum Fault {
            case none, operationCancelled, queryFailed, cancelDuringQuery, receiverChangedAfterHover,
                 readinessLostAfterHover, refusedAfterFinalQuery, closeOnDown, closeBeforeSecondClick,
                 processGoneOnDown, processGoneBeforeSecondClick
        }
        let fault: Fault
        let point = CGPoint(x: 25, y: 35)
        var posts = [CGEventType]()
        var operationChecks = 0, queries = 0, targetChecks = 0, physicalChecks = 0
        var releaseChecks = 0, skippedReleases = 0, marked = 0, returned = 0
        var targetExists = true, processExists = true, cancelled = false, boundaryConflict = false
        var stages = [String]()

        init(_ fault: Fault = .none) { self.fault = fault }

        func run() async throws {
            let request = try NativePointerIntent(
                unit: "pointer_sequence", kind: "click",
                parameters: ["x": 25, "y": 35,
                             "count": [.closeBeforeSecondClick, .processGoneBeforeSecondClick].contains(fault) ? 2 : 1],
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
                            // A final query can change the facts consumed by
                            // the supplied event guard.
                            if self.fault == .refusedAfterFinalQuery && self.posts.contains(.mouseMoved) {
                                self.boundaryConflict = true
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
                            if self.boundaryConflict {
                                throw DriverError("event guard refused", code: .inputUnavailable)
                            }
                        }, unavailable: { DriverError("receiver not ready", code: .inputUnavailable) })
                }, post: { event, point in
                    precondition(point == self.point)
                    self.posts.append(event.type)
                    if event.type == .leftMouseDown {
                        if [.closeOnDown, .closeBeforeSecondClick].contains(self.fault) { self.targetExists = false }
                        if [.processGoneOnDown, .processGoneBeforeSecondClick].contains(self.fault) {
                            self.processExists = false
                        }
                    }
                }, canRelease: {
                    self.releaseChecks += 1
                    return self.processExists
                }, skipped: { self.skippedReleases += 1 },
                onDispatch: { self.marked += 1 }, onPosted: { self.returned += 1 })
        }
    }

    @MainActor static func main() async throws {
        try await secondPairContinuesOnlyAfterTheFirstUp()
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
        for fault in [Scene.Fault.receiverChangedAfterHover, .readinessLostAfterHover, .refusedAfterFinalQuery] {
            let scene = Scene(fault)
            do { try await scene.run(); preconditionFailure("a refused event received down") }
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

        for fault in [Scene.Fault.processGoneOnDown, .processGoneBeforeSecondClick] {
            let dead = Scene(fault)
            do { try await dead.run(); preconditionFailure("unknown release was reported as success") }
            catch let error as DriverError { precondition(error.code == .inputUnavailable) }
            precondition(dead.posts.map(\.rawValue) == [8, 5, 1] && dead.skippedReleases == 1,
                         "cleanup cannot release into a replacement process or allow a second down")
            precondition(dead.marked == 3 && dead.returned == 3,
                         "the already dispatched events must retain their progress")
        }
        try nonClickBoundariesRemainExplicit()
        print("Pointer event query/final-target/held ordering and original paired release passed")
    }

    @MainActor static func secondPairContinuesOnlyAfterTheFirstUp() async throws {
        let point = CGPoint(x: 15, y: 20)
        let request = try NativePointerIntent(
            unit: "pointer_sequence", kind: "click", parameters: ["button": "left", "count": 2],
            foreground: false, element: true)
        guard let source = CGEventSource(stateID: .privateState) else { preconditionFailure("no event source") }
        let sequence = try NativePointerEvents(request: request, from: point, to: nil,
                                               viewport: CGSize(width: 100, height: 100), source: source)
        for releaseSucceeds in [true, false] {
            var events = [(CGEventType, Int)]()
            var destination = 1
            var continuationCount = 0
            do {
                try await sequence.deliver(
                    check: { _, _ in
                        guard !events.contains(where: { $0.0 == .leftMouseUp }) else {
                            throw DriverError("the new editor covers the original cell", code: .staleTarget)
                        }
                    }, post: { event, postedPoint in
                        precondition(postedPoint == point)
                        events.append((event.type, destination))
                    }, canRelease: { releaseSucceeds }, skipped: {}, onDispatch: {}, onPosted: {},
                    validateSecondDown: { suppliedPoint in
                        precondition(suppliedPoint == point)
                        precondition(events.map(\.0) == [.mouseMoved, .leftMouseDown, .leftMouseUp])
                        continuationCount += 1
                        destination = 2
                        return true
                    })
                precondition(releaseSucceeds)
            } catch let error as DriverError {
                precondition(!releaseSucceeds && error.code == .inputUnavailable)
            }
            if releaseSucceeds {
                precondition(continuationCount == 1)
                precondition(events.map(\.0) == [.mouseMoved, .leftMouseDown, .leftMouseUp, .leftMouseDown, .leftMouseUp])
                precondition(events.map(\.1) == [1, 1, 1, 2, 2])
            } else {
                precondition(continuationCount == 0)
                precondition(events.map(\.0) == [.mouseMoved, .leftMouseDown])
            }
        }
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
