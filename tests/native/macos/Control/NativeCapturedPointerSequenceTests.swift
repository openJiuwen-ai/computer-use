// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices

@main enum NativeCapturedPointerSequenceTests {
    @MainActor private static func mapping(
        frame: CGRect, width: Int = 840, height: Int = 440
    ) -> WindowGeometry.WindowMapping {
        .init(id: 71, pid: 72, frame: frame, title: "Original", width: width, height: height)
    }

    @MainActor private static func probe(
        change: CapturedPointerSequenceProbe.Change = .none
    ) throws -> CapturedPointerSequenceProbe {
        let original = mapping(frame: CGRect(x: 100, y: 200, width: 420, height: 220))
        // Native queries report current geometry, not the retained PNG's pixels.
        let current = mapping(frame: CGRect(x: 350, y: 310, width: 420, height: 220), width: 420, height: 220)
        let plan = try NativeCapturedPointerPlan.resolve(original: original, current: current)
        return CapturedPointerSequenceProbe(plan: plan, current: current, change: change)
    }

    @MainActor private static func request(_ kind: String, count: Int = 1) throws -> NativePointerIntent {
        let parameters: [String: Any]
        switch kind {
        case "click": parameters = ["x": 420, "y": 220, "count": count]
        case "scroll": parameters = ["x": 420, "y": 220, "direction": "down", "pages": 0.5]
        case "drag": parameters = ["fromX": 420, "fromY": 220, "toX": 700, "toY": 360]
        default: preconditionFailure("unexpected test intent")
        }
        return try NativePointerIntent(unit: "pointer_sequence", kind: kind, parameters: parameters, foreground: false)
    }

    @MainActor private static func requireOriginalRoute(_ scene: CapturedPointerSequenceProbe) {
        precondition(scene.posts.allSatisfy { $0.windowID == 71 && $0.pid == 72 },
                     "neither new input nor cleanup may be redirected to another native window or process")
        precondition(scene.posts.allSatisfy {
            $0.event.location == $0.point
                && $0.localPoint == CGPoint(x: $0.point.x - $0.mapping.frame.minX,
                                           y: $0.point.y - $0.mapping.frame.minY)
        }, "screen coordinates and current window-local routing coordinates must remain distinct")
    }

    @MainActor private static func translatedClick(count: Int) async throws {
        let scene = try probe()
        try await scene.deliver(request("click", count: count))
        let expected: [CGEventType] = count == 1
            ? [.mouseMoved, .leftMouseDown, .leftMouseUp]
            : [.mouseMoved, .leftMouseDown, .leftMouseUp, .leftMouseDown, .leftMouseUp]
        precondition(scene.types == expected, "one click intent must send exactly its requested click train")
        let point = CGPoint(x: 560, y: 420)
        precondition(scene.posts.allSatisfy { $0.point == point })
        let clicks = scene.posts.filter { $0.event.type != .mouseMoved }
        precondition(clicks.map { $0.event.getIntegerValueField(.mouseEventClickState) }
                     == (count == 1 ? [1, 1] : [1, 1, 2, 2]))
        precondition(scene.dispatches == 1 + count && scene.accepted == 1 + count)
        precondition(scene.releaseChecks == count && scene.skippedReleases == 0 && scene.deliveries == 1)
        requireOriginalRoute(scene)
    }

    @MainActor private static func translatedScroll() async throws {
        let scene = try probe()
        try await scene.deliver(request("scroll"))
        precondition(scene.types == [.mouseMoved, .scrollWheel], "scroll must not degrade into a click")
        precondition(scene.posts.allSatisfy { $0.point == CGPoint(x: 560, y: 420) })
        precondition(scene.dispatches == 2 && scene.accepted == 2)
        precondition(scene.releaseChecks == 0 && scene.skippedReleases == 0 && scene.deliveries == 1)
        requireOriginalRoute(scene)
    }

    @MainActor private static func completedDrag(_ change: CapturedPointerSequenceProbe.Change) async throws {
        let scene = try probe(change: change)
        try await scene.deliver(request("drag"))
        let start = CGPoint(x: 560, y: 420), end = CGPoint(x: 700, y: 490)
        precondition(Array(scene.types.prefix(2)) == [.mouseMoved, .leftMouseDown])
        precondition(scene.types.last == .leftMouseUp)
        precondition(scene.posts[0].point == start && scene.posts[1].point == start)
        let motion = scene.posts.filter { $0.event.type == .leftMouseDragged }
        precondition(!motion.isEmpty && motion.last?.point == end,
                     "the held drag must reach its original screen endpoint even when it moves or resizes its window")
        precondition(motion.first?.point == start, "drag tracking starts without displacing its press anchor")
        precondition(motion.dropFirst().allSatisfy {
            $0.point.x > start.x && $0.point.x <= end.x && $0.point.y > start.y && $0.point.y <= end.y
        }, "a changed origin or extent must not translate or rescale the already resolved screen path")
        precondition(scene.posts.count == motion.count + 3)
        precondition(scene.posts.last?.point == end, "paired release must use the final delivered screen point")
        precondition(scene.dispatches == scene.posts.count - 1 && scene.accepted == scene.dispatches)
        precondition(scene.releaseChecks == 1 && scene.skippedReleases == 0 && scene.deliveries == 1)
        precondition(scene.checkedHeld.prefix(2).allSatisfy { !$0 } && scene.checkedHeld.dropFirst(2).allSatisfy { $0 })
        if change == .afterDown || change == .afterFirstDrag {
            precondition(scene.posts.last?.localPoint == CGPoint(x: 270, y: 220),
                         "after a window translation, local coordinates must consume its new origin")
        }
        if change == .followEveryDrag {
            precondition(scene.posts.last?.localPoint == CGPoint(x: 210, y: 110),
                         "mouse-up must use the origin established by the final consumed drag event")
            precondition(scene.posts.last?.localPoint == scene.posts[1].localPoint,
                         "a window following the pointer must release at the original window-local anchor")
            precondition(scene.posts.last?.mapping.frame.origin == CGPoint(x: 490, y: 380))
            precondition(scene.posts.last?.mapping.frame != motion.last?.mapping.frame,
                         "release geometry must refresh after the final drag, not reuse its pre-dispatch frame")
        }
        requireOriginalRoute(scene)
    }

    @MainActor private static func changeAfterMoveStops(_ kind: String) async throws {
        let scene = try probe(change: .afterMove)
        do {
            try await scene.deliver(request(kind, count: 2))
            preconditionFailure("geometry must remain strict before the first press or wheel event")
        } catch is ObservationChanged {}
        precondition(scene.types == [.mouseMoved], "stale initial geometry must not send a press, wheel, or replay")
        precondition(scene.checkedHeld == [false, false])
        precondition(scene.dispatches == 1 && scene.accepted == 1 && scene.deliveries == 1)
        precondition(scene.releaseChecks == 0 && scene.skippedReleases == 0)
        requireOriginalRoute(scene)
    }

    @MainActor private static func clickDoesNotAcquireDragGeometryPermission() async throws {
        let scene = try probe(change: .afterDown)
        do {
            try await scene.deliver(request("click", count: 2))
            preconditionFailure("a click must recheck strict geometry before a second mouse-down")
        } catch is ObservationChanged {}
        precondition(scene.types == [.mouseMoved, .leftMouseDown, .leftMouseUp],
                     "the first click must be paired, but changed geometry must not receive a second click")
        precondition(scene.checkedHeld == [false, false, false])
        precondition(scene.releaseChecks == 1 && scene.skippedReleases == 0)
        precondition(scene.dispatches == 2 && scene.accepted == 2 && scene.deliveries == 1)
        requireOriginalRoute(scene)
    }

    @MainActor private static func interruptedDrag(_ change: CapturedPointerSequenceProbe.Change) async throws {
        let scene = try probe(change: change)
        do {
            try await scene.deliver(request("drag"))
            preconditionFailure("a held drag must stop on identity, geometry validity, or logical boundary failure")
        } catch is ObservationChanged {
            precondition([.windowChanged, .ownerChanged, .invalidFrame].contains(change))
        } catch is CapturedPointerSequenceProbe.BoundaryFailure {
            precondition([.windowGone, .modalBlocked, .processGone].contains(change))
        }
        let expected: [CGEventType] = change == .processGone
            ? [.mouseMoved, .leftMouseDown, .leftMouseDragged, .leftMouseDragged]
            : [.mouseMoved, .leftMouseDown, .leftMouseDragged, .leftMouseDragged, .leftMouseUp]
        precondition(scene.types == expected, "interruption may release only its own press; it must never replay")
        let lastDelivered = scene.posts.last { $0.event.type == .leftMouseDragged }!.point
        precondition(lastDelivered != CGPoint(x: 700, y: 490))
        if change == .processGone {
            precondition(scene.skippedReleases == 1, "a dead original process must not receive a recycled-PID release")
        } else {
            precondition(scene.posts.last?.point == lastDelivered,
                         "even a vanished window must release via the captured live process at the last screen point")
            precondition(scene.skippedReleases == 0)
        }
        precondition(scene.checkedHeld.last == true && scene.releaseChecks == 1)
        precondition(scene.dispatches == 4 && scene.accepted == 4 && scene.deliveries == 1)
        requireOriginalRoute(scene)
    }

    @MainActor static func main() async throws {
        try dragDisplacementDoesNotAccumulateRounding()
        try await translatedClick(count: 1)
        try await translatedClick(count: 2)
        try await translatedScroll()
        for kind in ["click", "scroll", "drag"] { try await changeAfterMoveStops(kind) }
        try await clickDoesNotAcquireDragGeometryPermission()
        for change: CapturedPointerSequenceProbe.Change in [
            .none, .afterDown, .afterFirstDrag, .resizeAfterDown, .resizeAfterFirstDrag, .followEveryDrag
        ] { try await completedDrag(change) }
        for change: CapturedPointerSequenceProbe.Change in [
            .windowChanged, .ownerChanged, .windowGone, .modalBlocked, .processGone, .invalidFrame
        ] { try await interruptedDrag(change) }
        print("Captured pointer event sequence regressions passed")
    }

    @MainActor private static func dragDisplacementDoesNotAccumulateRounding() throws {
        let from = CGPoint(x: 370.25, y: 1053.5)
        for delta in [CGPoint(x: 60, y: 0), CGPoint(x: -61, y: 33), CGPoint(x: 3.5, y: -2.5)] {
            let to = CGPoint(x: from.x + delta.x, y: from.y + delta.y)
            let sequence = try NativePointerEvents(
                request: request("drag"), from: from, to: to, viewport: CGSize(width: 480, height: 242),
                source: CGEventSource(stateID: .privateState)!)
            let events = sequence.items.flatMap { [$0.event, $0.release].compactMap { $0 } }
            let sumX = events.reduce(Int64(0)) { $0 + $1.getIntegerValueField(.mouseEventDeltaX) }
            let sumY = events.reduce(Int64(0)) { $0 + $1.getIntegerValueField(.mouseEventDeltaY) }
            precondition(sumX == Int64(delta.x.rounded()) && sumY == Int64(delta.y.rounded()),
                         "relative input must agree with the requested complete path")
            for event in events where event.type != .leftMouseDragged {
                precondition(event.getIntegerValueField(.mouseEventDeltaX) == 0)
                precondition(event.getIntegerValueField(.mouseEventDeltaY) == 0)
            }
        }
    }
}

@MainActor private final class CapturedPointerSequenceProbe {
    enum Change: Equatable {
        case none, afterMove, afterDown, afterFirstDrag, resizeAfterDown, resizeAfterFirstDrag, followEveryDrag
        case windowChanged, ownerChanged, windowGone, modalBlocked, processGone, invalidFrame
    }
    enum BoundaryFailure: Error { case windowGone, modalBlocked, processGone }
    struct Posted {
        let event: CGEvent
        let point: CGPoint
        let localPoint: CGPoint
        let mapping: WindowGeometry.WindowMapping
        let windowID: CGWindowID
        let pid: pid_t
    }

    let plan: NativeCapturedPointerPlan
    var currentFrame: WindowGeometry.WindowMapping
    var lastConfirmedFrame: WindowGeometry.WindowMapping
    let change: Change
    var posts = [Posted]()
    var checkedHeld = [Bool]()
    var deliveries = 0, dispatches = 0, accepted = 0, releaseChecks = 0, skippedReleases = 0
    private var changed = false
    private var windowExists = true, processAlive = true, blocked = false
    private var firstScreenPoint: CGPoint?

    var types: [CGEventType] { posts.map { $0.event.type } }

    init(plan: NativeCapturedPointerPlan, current: WindowGeometry.WindowMapping, change: Change) {
        self.plan = plan
        currentFrame = current
        lastConfirmedFrame = current
        self.change = change
    }

    private var usableCurrentFrame: Bool {
        currentFrame.id == plan.mapping.id && currentFrame.pid == plan.mapping.pid && windowExists
            && [currentFrame.frame.minX, currentFrame.frame.minY, currentFrame.frame.width, currentFrame.frame.height]
                .allSatisfy(\.isFinite) && currentFrame.frame.width > 0 && currentFrame.frame.height > 0
    }

    private func changeAfter(_ event: CGEvent) {
        if change == .followEveryDrag, event.type == .leftMouseDragged, let firstScreenPoint {
            let frame = plan.mapping.frame.offsetBy(dx: event.location.x - firstScreenPoint.x,
                                                   dy: event.location.y - firstScreenPoint.y)
            currentFrame = .init(id: currentFrame.id, pid: currentFrame.pid, frame: frame, title: currentFrame.title,
                                 width: currentFrame.width, height: currentFrame.height)
            return
        }
        let afterDown = [.afterDown, .resizeAfterDown].contains(change) && event.type == .leftMouseDown
        let afterMove = change == .afterMove && event.type == .mouseMoved
        let afterDrag = ![.none, .afterMove, .afterDown, .resizeAfterDown].contains(change)
            && event.type == .leftMouseDragged && event.location != firstScreenPoint
        guard !changed && (afterDown || afterMove || afterDrag) else { return }
        changed = true
        let old = currentFrame
        var frame = old.frame, id = old.id, pid = old.pid
        switch change {
        case .afterMove, .afterDown, .afterFirstDrag: frame = frame.offsetBy(dx: 80, dy: -40)
        case .resizeAfterDown, .resizeAfterFirstDrag: frame.size = CGSize(width: 640, height: 330)
        case .windowChanged: id = 73
        case .ownerChanged: pid = 74
        case .windowGone: windowExists = false
        case .modalBlocked: blocked = true
        case .processGone: processAlive = false
        case .invalidFrame: frame = CGRect(x: CGFloat.nan, y: 310, width: 420, height: 220)
        case .none, .followEveryDrag: break
        }
        currentFrame = .init(id: id, pid: pid, frame: frame, title: old.title, width: old.width, height: old.height)
    }

    func deliver(_ request: NativePointerIntent) async throws {
        deliveries += 1
        firstScreenPoint = try plan.point(request.start!)
        let source = CGEventSource(stateID: .privateState)!
        let sequence = try NativePointerEvents(
            request: request, from: plan.point(request.start!), to: request.end.map { try plan.point($0) },
            viewport: plan.mapping.frame.size, source: source)
        try await sequence.deliver(
            check: { _, held in
                self.checkedHeld.append(held)
                guard self.processAlive else { throw BoundaryFailure.processGone }
                guard self.windowExists else { throw BoundaryFailure.windowGone }
                guard !self.blocked else { throw BoundaryFailure.modalBlocked }
                try self.plan.requireCurrent(self.currentFrame, continuingDrag: request.kind == .drag && held)
                self.lastConfirmedFrame = self.currentFrame
            },
            post: { event, point in
                // Consume real event construction and the production coordinate
                // conversion, without posting OS input to any destination.
                event.location = point
                let mapping = self.usableCurrentFrame ? self.currentFrame : self.lastConfirmedFrame
                let local = BackgroundEvents.pointerLocation(point, window: mapping)
                self.posts.append(Posted(event: event, point: point, localPoint: local, mapping: mapping,
                                         windowID: self.plan.mapping.id, pid: self.plan.mapping.pid))
                self.changeAfter(event)
            },
            canRelease: { self.releaseChecks += 1; return self.processAlive },
            skipped: { self.skippedReleases += 1 },
            onDispatch: { self.dispatches += 1 }, onPosted: { self.accepted += 1 })
    }
}
