// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices

@main enum NativeCapturedPointerPlanTests {
    @MainActor static func mapping(
        id: CGWindowID = 71, pid: pid_t = 72,
        frame: CGRect = CGRect(x: 100, y: 200, width: 420, height: 220),
        width: Int = 840, height: Int = 440
    ) -> WindowGeometry.WindowMapping {
        .init(id: id, pid: pid, frame: frame, title: "Original", width: width, height: height)
    }

    @MainActor static func refuses(_ body: () throws -> Void) {
        do {
            try body()
            preconditionFailure("unconfirmed identity or geometry must stop this input phase")
        } catch {}
    }

    @MainActor static func main() throws {
        let original = mapping()
        let translated = mapping(frame: CGRect(x: 350, y: 310, width: 420, height: 220), width: 420, height: 220)
        let plan = try NativeCapturedPointerPlan.resolve(original: original, current: translated)
        precondition(plan.mapping.width == 840 && plan.mapping.height == 440,
                     "a current native extent is not the retained screenshot's pixel extent")
        let point = try plan.point(CGPoint(x: 420, y: 220))
        precondition(point == CGPoint(x: 560, y: 420))
        precondition(original.frame.origin == CGPoint(x: 100, y: 200), "the original capture must remain immutable")
        let scaled = mapping(width: 210, height: 110)
        let small = try NativeCapturedPointerPlan.resolve(original: scaled, current: translated)
        let scaledPoint = try small.point(CGPoint(x: 105, y: 55))
        precondition(scaledPoint == CGPoint(x: 560, y: 420))
        for changed in [
            mapping(id: 73), mapping(pid: 74),
            mapping(frame: CGRect(x: 100, y: 200, width: 421, height: 220)),
            mapping(frame: CGRect(x: 100, y: 200, width: 420, height: 221)),
        ] {
            refuses { _ = try NativeCapturedPointerPlan.resolve(original: original, current: changed) }
        }
        try plan.requireCurrent(translated)
        let movedDuringDrag = mapping(frame: translated.frame.offsetBy(dx: 80, dy: -40))
        let resizedDuringDrag = mapping(frame: CGRect(x: 430, y: 270, width: 640, height: 330))
        for changed in [movedDuringDrag, resizedDuringDrag] {
            refuses { try plan.requireCurrent(changed) }
            refuses { try plan.requireCurrent(changed, continuingDrag: false) }
            try plan.requireCurrent(changed, continuingDrag: true)
        }
        let retainedPoint = try plan.point(CGPoint(x: 420, y: 220))
        precondition(retainedPoint == point,
                     "movement after mouse-down must not remap the already resolved screen path")
        precondition(BackgroundEvents.pointerLocation(point, window: translated) == CGPoint(x: 210, y: 110))
        precondition(BackgroundEvents.pointerLocation(point, window: movedDuringDrag) == CGPoint(x: 130, y: 150),
                     "window-local routing coordinates must use the current origin, independently of the screen path")
        precondition(BackgroundEvents.pointerLocation(point, window: resizedDuringDrag) == CGPoint(x: 130, y: 150),
                     "a held drag's screen location must not be scaled to the new window size")
        for changed in [
            mapping(id: 73, frame: translated.frame), mapping(pid: 74, frame: translated.frame),
            mapping(frame: CGRect(x: CGFloat.nan, y: 310, width: 420, height: 220)),
            mapping(frame: CGRect(x: 350, y: CGFloat.infinity, width: 420, height: 220)),
            mapping(frame: CGRect(x: 350, y: 310, width: CGFloat.infinity, height: 220)),
            mapping(frame: CGRect(x: 350, y: 310, width: 420, height: CGFloat.nan)),
            mapping(frame: CGRect(x: 350, y: 310, width: 0, height: 220)),
            mapping(frame: CGRect(x: 350, y: 310, width: 420, height: 0)),
        ] {
            refuses { try plan.requireCurrent(changed) }
            refuses { try plan.requireCurrent(changed, continuingDrag: true) }
        }
        refuses { _ = try plan.point(CGPoint(x: 840, y: 0)) }
        refuses { _ = try plan.point(CGPoint(x: -1, y: 0)) }
        print("Captured pointer translation plan regressions passed")
    }
}
