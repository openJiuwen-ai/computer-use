// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices

@main enum NativeCoordinateMenuPressTests {
    @MainActor static func check(_ condition: @autoclosure () throws -> Bool, _ message: String = "condition failed") throws {
        let value = try condition()
        precondition(value, message)
    }

    @MainActor static func main() throws {
        let item = AXUIElementCreateApplication(10001)
        let menu = AXUIElementCreateApplication(10002)
        let label = AXUIElementCreateApplication(10003)
        let submenu = AXUIElementCreateApplication(10004)
        let point = CGPoint(x: 80, y: 50)
        let window = WindowGeometry.WindowMapping(id: 1, pid: 10,
            frame: CGRect(x: 0, y: 0, width: 150, height: 100), title: nil, width: 150, height: 100)
        var hit = item, supported = true, presented = true, visible = true, enabled: Bool? = true
        var child: AXUIElement? = nil, itemRole = kAXMenuItemRole, itemPID: pid_t = 10, calls = 0
        var q = NativeCoordinateMenuPress.Queries()
        q.hit = { _, _ in calls += 1; return hit }
        q.role = { CFEqual($0, item) ? itemRole : CFEqual($0, label) ? kAXStaticTextRole : kAXMenuRole }
        q.parent = { CFEqual($0, label) ? item : CFEqual($0, item) ? menu : nil }
        q.children = { _ in child.map { [$0] } ?? [] }
        q.visible = { _ in visible ? [item] : [] }
        q.frame = { _ in CGRect(x: 20, y: 30, width: 100, height: 40) }
        q.enabled = { _ in enabled }
        q.press = { _ in supported }
        q.pid = { CFEqual($0, item) ? itemPID : 10 }
        q.presented = { _, _ in presented }
        func matches() throws -> Bool { try NativeCoordinateMenuPress.candidate(at: point, window: window, queries: q) != nil }
        try check(try matches(), "ordinary visible menu item must supply coordinate Press")
        hit = label
        try check(try matches(), "decorative text hit belongs to its exact parent menu item")
        hit = item
        child = submenu
        try check(try !matches(), "submenu opener cannot become an ordinary choice")
        child = nil; supported = false
        try check(try !matches(), "missing Press leaves pointer route independent")
        supported = true; presented = false
        try check(try !matches(), "hidden static menu cannot hijack coordinates")
        presented = true; visible = false
        try check(try !matches(), "stale menu membership must not press")
        visible = true; enabled = false
        try check(try !matches())
        enabled = nil
        try check(try matches(), "unknown enabled does not add an invented global barrier")
        itemRole = kAXTextFieldRole
        try check(try !matches(), "text caret clicks retain pointer semantics")
        itemRole = kAXMenuItemRole; itemPID = 11
        try check(try !matches(), "another process is not the captured menu")
        itemPID = 10
        q.frame = { _ in CGRect(x: 500, y: 500, width: 100, height: 40) }
        try check(try !matches(), "no snapping to a nearby AX menu item")
        q.hit = { _, _ in nil }
        try check(try !matches(), "unavailable AX leaves the original pointer route")
        let intent: ([String: Any], Bool) throws -> NativeIntent = { parameters, global in
            try NativeIntent(["kind": "click", "parameters": parameters, "globalInput": global], mode: "write")
        }
        let ordinary: [String: Any] = ["x": 80, "y": 50]
        try check(NativeCoordinateMenuPress.permits(try intent(ordinary, false)))
        for extra: [String: Any] in [
            ["button": "right"], ["button": "middle"], ["count": 2], ["pointerOnly": true],
            ["key": "shift"], ["holdDuration": 1], ["fromX": 10]
        ] {
            let parameters = ordinary.merging(extra) { _, new in new }
            try check(!NativeCoordinateMenuPress.permits(try intent(parameters, false)))
        }
        try check(!NativeCoordinateMenuPress.permits(try intent(ordinary, true)))
        try check(!NativeCoordinateMenuPress.permits(try intent([:], false)))
        let pointer = try NativePointerIntent(unit: "pointer_sequence", kind: "click",
            parameters: ["x": 80, "y": 50, "pointerOnly": true], foreground: false)
        precondition(pointer.start == point && pointer.button == "left" && pointer.count == 1)
        do {
            _ = try NativePointerIntent(unit: "pointer_sequence", kind: "click",
                parameters: ["x": 80, "y": 50, "pointerOnly": "yes"], foreground: false)
            preconditionFailure("pointerOnly must be an actual boolean")
        } catch {}
        func row(id: UInt32, pid: Int = 10, popup: Bool = true) -> [String: Any] {
            [kCGWindowNumber as String: NSNumber(value: id), kCGWindowOwnerPID as String: NSNumber(value: pid),
             kCGWindowLayer as String: NSNumber(value: popup ? Int(CGWindowLevelForKey(.popUpMenuWindow)) : 0),
             kCGWindowBounds as String: window.frame.dictionaryRepresentation]
        }
        precondition(NativeMenuSurfaces.matchesPhysicalWindow(window, rows: [row(id: 1)]))
        precondition(!NativeMenuSurfaces.matchesPhysicalWindow(window, rows: [row(id: 2)]),
                     "matching geometry cannot relabel a different popup as the original image")
        precondition(!NativeMenuSurfaces.matchesPhysicalWindow(window, rows: [row(id: 1), row(id: 2)]),
                     "two same-process overlapping popups are not a unique physical association")
        precondition(!NativeMenuSurfaces.matchesPhysicalWindow(window, rows: [row(id: 1, pid: 11)]))
        precondition(!NativeMenuSurfaces.matchesPhysicalWindow(window, rows: [row(id: 1, popup: false)]))
        precondition(!NativeMenuSurfaces.matchesPhysicalWindow(window, rows: nil))
        try failureContinuation(item: item, menu: menu)
        print("PASS coordinate-menu local applicability; unchanged raw coordinates; unavailable/submenu/caret/global exclusions (\(calls) optional hits)")
    }
    @MainActor static func failureContinuation(item: AXUIElement, menu: AXUIElement) throws {
        let binding: [String: Any] = ["session": "s", "instance": "a", "rootWindow": "w", "surface": "w",
                                      "node": NSNull(), "capture": "image"]
        let intent = try NativeIntent(["kind": "click", "parameters": ["x": 80, "y": 50]], mode: "write")
        let command = try NativeUnitCommand(["kind": "native_action", "primitiveId": "press", "target": binding,
                                             "foreground": false])
        for phase in ["before", "after", "cancel", "sourceGone"] {
            let op = NativeOperation(timeoutMs: 10000, writable: true, intent: intent, target: binding,
                                     clickContract: "v2", release: {}, cancelActive: {})
            let proof = NativeCoordinateMenuPress(element: item, menu: menu, application: .current,
                capture: "image", pixels: CGPoint(x: 80, y: 50), windowID: 1, attempt: op.coordinateMenuPressAttempt)
            let receipt = NativeExecutionReceipt(target: binding, onEffect: {})
            let evidence = NativeClickEvidence(target: binding, family: NativeCoordinateMenuPress.family)
            if phase == "after" {
                evidence.markCall("nativeAction")
                receipt.dispatch = "unknown"
            }
            let error: Error = phase == "cancel" ? CancellationError()
                : phase == "sourceGone" ? DriverError("original capture ended", code: .staleTarget)
                : DriverError("menu capability changed", code: .inputUnavailable)
            BuildNativeClickFailurePass.run(error, command: command, operation: op, step: 1,
                offer: proof.offer, within: [], receipt: receipt, evidence: evidence)
            precondition(op.coordinateMenuPressAttempt.rejectedBeforeDispatch == (phase == "before"))
            precondition((receipt.retryReason == "preconditionChanged") == (phase == "before"))
            let continuation = evidence.wire["continuation"] as! [String: Any]
            if phase == "before" {
                precondition(continuation["kind"] as? String == "reselectWithinScope",
                             "no-effect plugin failure must refresh to independent pointer, not same Press")
            } else {
                precondition(receipt.retryReason == nil, "possible effect, cancellation or ended source cannot replay")
            }
        }
    }

}
