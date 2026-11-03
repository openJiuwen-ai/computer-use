// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices

/// Some providers flatten scrolling content and its scroll bar under the
/// window. A real bar gives a wheel receiver and viewport span. If the provider
/// hides its overlay bar, a bounded wheel over observed passive background can
/// discover scrolling; only progress of the original element confirms it.
@MainActor enum NativeWindowScrollExposure {
    struct Candidate {
        let viewport: CGRect
        let point: CGPoint
        let direction: String
        let pages: Double
        let distance: Double
    }

    nonisolated static func candidate(frame: CGRect, bar: CGRect, window: CGRect, orientation: String?) -> Candidate? {
        let visible = bar.intersection(window)
        guard !visible.isNull, !visible.isEmpty else { return nil }
        let vertical: Bool
        switch orientation {
        case kAXVerticalOrientationValue: vertical = true
        case kAXHorizontalOrientationValue: vertical = false
        case nil:
            // A provider may omit Orientation while exposing the actual bar
            // geometry. A square has no inferred axis.
            guard bar.width != bar.height else { return nil }
            vertical = bar.height > bar.width
        default: return nil
        }
        let viewport = vertical
            ? CGRect(x: window.minX, y: visible.minY, width: window.width, height: visible.height)
            : CGRect(x: visible.minX, y: window.minY, width: visible.width, height: window.height)
        let aligned = vertical
            ? frame.maxX > viewport.minX && frame.minX < viewport.maxX
            : frame.maxY > viewport.minY && frame.minY < viewport.maxY
        guard aligned, let movement = NativeElementViewport.movement(frame: frame, viewport: viewport),
            vertical == ["up", "down"].contains(movement.direction)
        else { return nil }
        return Candidate(
            viewport: viewport, point: CGPoint(x: visible.midX, y: visible.midY),
            direction: movement.direction, pages: min(1, movement.pages), distance: movement.distance)
    }

    static func sample(
        frame: CGRect, window: AXUIElement, mapping: WindowGeometry.WindowMapping
    ) throws -> NativeElementViewport.Exposure? {
        let deadline = ProcessInfo.processInfo.systemUptime + 0.15
        var bars = [AXUIElement]()
        for key in [kAXVerticalScrollBarAttribute, kAXHorizontalScrollBarAttribute] {
            if let bar = AXAccess.element(window, key) { bars.append(bar) }
        }
        // Complete direct children only. This is local to the flattened-window
        // fallback, never a walk through a virtual document or another window.
        if let children = AXAccess.elements(window, kAXChildrenAttribute, limit: 256) {
            for child in children {
                try RequestDiagnostics.checkCancellation()
                guard ProcessInfo.processInfo.systemUptime < deadline else { return nil }
                if AXAccess.read(child, kAXRoleAttribute).value as? String == kAXScrollBarRole,
                    !bars.contains(where: { CFEqual($0, child) })
                { bars.append(child) }
            }
        }
        var candidates = [(AXUIElement, Candidate)]()
        for bar in bars {
            try RequestDiagnostics.checkCancellation()
            guard ProcessInfo.processInfo.systemUptime < deadline else { return nil }
            guard AXAccess.read(bar, kAXEnabledAttribute).value as? Bool != false,
                AXAccess.read(bar, "AXHidden").value as? Bool != true,
                let bounds = WindowGeometry.hint(bar)?.frame,
                let candidate = candidate(
                    frame: frame, bar: bounds, window: mapping.frame,
                    orientation: AXAccess.read(bar, kAXOrientationAttribute).value as? String)
            else { continue }
            candidates.append((bar, candidate))
        }
        // Multiple independent scrollers do not establish which content owns
        // this flattened target. Keep other click routes available.
        if bars.isEmpty {
            return try backgroundProbe(frame: frame, window: window, mapping: mapping)
        }
        guard candidates.count == 1, let (bar, candidate) = candidates.first else { return nil }
        return .init(
            mapping: mapping, region: bar, viewport: candidate.viewport, frame: frame,
            direction: candidate.direction, pages: candidate.pages, distance: candidate.distance,
            inputPoint: candidate.point)
    }

    nonisolated static func probePoints(frame: CGRect, viewport: CGRect) -> [CGPoint] {
        // A wheel on one axis cannot explain a diagonally separate target.
        let xAligned = frame.maxX > viewport.minX && frame.minX < viewport.maxX
        let yAligned = frame.maxY > viewport.minY && frame.minY < viewport.maxY
        guard xAligned != yAligned, !frame.intersects(viewport) else { return [] }
        return [0.5, 0.75, 0.25].flatMap { y in
            [0.5, 0.25, 0.75].map { x in
                CGPoint(x: viewport.minX + viewport.width * x, y: viewport.minY + viewport.height * y)
            }
        }
    }

    nonisolated static func passiveWheelReceiver(role: String?, actions: [String]?) -> Bool {
        guard let role, let actions,
            [kAXWindowRole, kAXGroupRole, kAXStaticTextRole, kAXScrollAreaRole].contains(role)
        else { return false }
        // In particular, never probe a text editor, stepper, slider or custom
        // actionable label: its wheel may edit a value instead of scroll.
        return actions.allSatisfy { $0 == kAXRaiseAction }
    }

    /// Rechecked at each event boundary, especially after mouseEntered/move
    /// have given the application a chance to replace a label with an editor.
    static func requirePassiveReceiver(
        at point: CGPoint, application: NSRunningApplication, window: AXUIElement, mapping: WindowGeometry.WindowMapping
    ) throws {
        try RequestDiagnostics.checkCancellation()
        let current = try NativeWindowUnit(application: application, window: window).mapping()
        guard current.id == mapping.id, current.pid == mapping.pid, current.frame == mapping.frame,
            passiveReceiver(at: point, window: window, pid: mapping.pid)
        else {
            throw DriverError("the scroll probe no longer has its original passive window receiver",
                              code: .inputUnavailable)
        }
        try RequestDiagnostics.checkCancellation()
    }

    private static func passiveReceiver(at point: CGPoint, window: AXUIElement, pid: pid_t) -> Bool {
        let root = AXUIElementCreateApplication(pid)
        var hit: AXUIElement?
        guard AXUIElementCopyElementAtPosition(root, Float(point.x), Float(point.y), &hit) == .success,
            let hit,
            CFEqual(hit, window) || WindowGeometry.owningWindow(hit, pid: pid, in: window)
                .map({ CFEqual($0, window) }) == true
        else { return false }
        var rawActions: CFArray?
        let status = AXUIElementCopyActionNames(hit, &rawActions)
        return passiveWheelReceiver(
            role: AXAccess.read(hit, kAXRoleAttribute).value as? String,
            actions: status == .success ? rawActions as? [String] : nil)
    }

    private static func backgroundProbe(
        frame: CGRect, window: AXUIElement, mapping: WindowGeometry.WindowMapping
    ) throws -> NativeElementViewport.Exposure? {
        guard let movement = NativeElementViewport.movement(frame: frame, viewport: mapping.frame) else { return nil }
        let deadline = ProcessInfo.processInfo.systemUptime + 0.15
        for point in probePoints(frame: frame, viewport: mapping.frame) {
            try RequestDiagnostics.checkCancellation()
            guard ProcessInfo.processInfo.systemUptime < deadline else { return nil }
            guard passiveReceiver(at: point, window: window, pid: mapping.pid) else { continue }
            return .init(
                mapping: mapping, region: window, viewport: mapping.frame, frame: frame,
                direction: movement.direction, pages: min(0.5, movement.pages), distance: movement.distance,
                inputPoint: point, probe: true)
        }
        return nil
    }
}
