// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices

/// Optional coordinate-entry plugin. Its negative result never changes the
/// pointer request. Only an already presented physical menu can supply a plan.
struct NativeCoordinateMenuPress {
    static let family = "macos.coordinateMenuPress.v1"
    let element: AXUIElement
    let menu: AXUIElement
    let application: NSRunningApplication
    let capture: String
    let pixels: CGPoint
    let windowID: CGWindowID
    @MainActor final class Attempt { var rejectedBeforeDispatch = false }
    let attempt: Attempt

    @MainActor struct Queries {
        var hit: (CGPoint, pid_t) -> AXUIElement? = { NativeInputWindowQuery.pointHit(at: $0, application: $1).element }
        var role: (AXUIElement) -> String? = { AXAccess.read($0, kAXRoleAttribute).value as? String }
        var parent: (AXUIElement) -> AXUIElement? = { AXAccess.element($0, kAXParentAttribute) }
        var children: (AXUIElement) -> [AXUIElement]? = { AXAccess.elements($0, kAXChildrenAttribute, limit: 256) }
        var visible: (AXUIElement) -> [AXUIElement]? = { AXAccess.elements($0, kAXVisibleChildrenAttribute, limit: 256) }
        var frame: (AXUIElement) -> CGRect? = { WindowGeometry.hint($0)?.frame }
        var enabled: (AXUIElement) -> Bool? = { AXAccess.read($0, kAXEnabledAttribute).value as? Bool }
        var press: (AXUIElement) -> Bool = {
            var actions: CFArray?
            return AXUIElementCopyActionNames($0, &actions) == .success
                && (actions as? [String])?.contains(kAXPressAction) == true
        }
        var pid: (AXUIElement) -> pid_t? = {
            var value: pid_t = 0
            return AXUIElementGetPid($0, &value) == .success ? value : nil
        }
        var presented: (AXUIElement, WindowGeometry.WindowMapping) -> Bool = {
            NativeMenuSurfaces.matches($0, window: $1)
        }
    }

    static func permits(_ intent: NativeIntent) -> Bool {
        let p = intent.parameters
        return !intent.globalInput && intent.kind == "click" && p["x"] != nil && p["y"] != nil
            && Set(p.keys).isSubset(of: ["x", "y", "button", "count", "pointerOnly"])
            && p["pointerOnly"] as? Bool != true && (p["button"] as? String ?? "left") == "left"
            && (p["count"] as? Int ?? 1) == 1
    }

    /// Same-process point hit and exact visible forward membership; no fuzzy
    /// nearest-element search, global hit, activation or menu opening.
    @MainActor static func candidate(
        at point: CGPoint, window: WindowGeometry.WindowMapping, queries q: Queries = Queries()
    ) throws -> (element: AXUIElement, menu: AXUIElement)? {
        try RequestDiagnostics.checkCancellation()
        guard var item = q.hit(point, window.pid) else { return nil }
        for _ in 0..<4 {
            let role = q.role(item)
            if role == kAXMenuItemRole { break }
            guard role == kAXStaticTextRole || role == kAXImageRole, let parent = q.parent(item) else { return nil }
            item = parent
        }
        guard q.role(item) == kAXMenuItemRole, q.pid(item) == window.pid,
            q.frame(item)?.contains(point) == true, q.enabled(item) != false,
            let children = q.children(item), children.allSatisfy({
                let role = q.role($0)
                return role == kAXStaticTextRole || role == kAXImageRole
            }), q.press(item), let menu = q.parent(item), q.role(menu) == kAXMenuRole,
            q.pid(menu) == window.pid, q.visible(menu)?.contains(where: { CFEqual($0, item) }) == true,
            q.presented(menu, window)
        else { return nil }
        try RequestDiagnostics.checkCancellation()
        return (item, menu)
    }

    @MainActor static func match(
        intent: NativeIntent, captured: NativeCapture.Input, point: CGPoint,
        attempt: Attempt
    ) throws -> Self? {
        guard !attempt.rejectedBeforeDispatch, permits(intent),
            captured.entry.surface.native?.level == Int(CGWindowLevelForKey(.popUpMenuWindow)),
            let capture = captured.entry.binding["capture"] as? String,
            let found = try candidate(at: point, window: captured.mapping)
        else { return nil }
        let app = captured.entry.surface.native?.application ?? captured.entry.surface.owner
        return Self(element: found.element, menu: found.menu, application: app, capture: capture,
                    pixels: CGPoint(x: try number(intent.parameters["x"], label: "x"),
                                    y: try number(intent.parameters["y"], label: "y")), windowID: captured.mapping.id,
                    attempt: attempt)
    }

    func matches(_ other: Self) -> Bool {
        capture == other.capture && pixels == other.pixels && windowID == other.windowID
            && CFEqual(element, other.element) && CFEqual(menu, other.menu)
    }

    @MainActor func requireCurrent(_ captured: NativeCapture.Input, queries: Queries = Queries()) throws {
        guard !attempt.rejectedBeforeDispatch,
            captured.entry.binding["capture"] as? String == capture, captured.mapping.id == windowID else {
            throw DriverError("coordinate menu source changed before Press", code: .inputUnavailable)
        }
        let point = try NativePointerUnit(capture: captured).point(pixels)
        guard let current = try Self.candidate(at: point, window: captured.mapping, queries: queries),
            CFEqual(current.element, element), CFEqual(current.menu, menu)
        else { throw DriverError("coordinate menu item changed before Press; no action sent", code: .inputUnavailable) }
    }

    var offer: NativeClickContract {
        var value = NativeClickContract(routeFamily: Self.family, delivery: .objectAction, admission: .ready,
                                        reason: nil, preparationOptions: [])
        value.coordinateMenuPress = self
        return value
    }
}
