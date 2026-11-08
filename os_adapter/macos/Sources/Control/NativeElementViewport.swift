// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices

/// One original element and its known clipping ancestors. A table bounds its
/// direct rows; ordinary wrapper frames do not clip. A scroll region can
/// prepare the original element for a click.
@MainActor struct NativeElementViewport {
    struct Exposure {
        let mapping: WindowGeometry.WindowMapping
        let region: AXUIElement
        let viewport: CGRect
        let frame: CGRect
        let direction: String
        let pages: Double
        let distance: Double
        var inputPoint: CGPoint? = nil
        var probe = false
        var point: CGPoint { inputPoint ?? CGPoint(x: viewport.midX, y: viewport.midY) }
    }

    struct VerticalClip {
        enum Edge { case top, bottom }
        let region: AXUIElement
        let viewport: CGRect
        let edge: Edge

        nonisolated static func edge(frame: CGRect, clipped: CGRect, viewport: CGRect) -> Edge? {
            guard !viewport.isNull, !viewport.isInfinite, viewport.height > 1,
                frame == clipped, frame.height > 0, frame.height <= 1, frame.width > 1,
                frame.minX > viewport.minX, frame.maxX < viewport.maxX, viewport.contains(frame)
            else { return nil }
            if frame.minY == viewport.minY { return .top }
            if frame.maxY == viewport.maxY { return .bottom }
            return nil
        }

        nonisolated static func isExposed(frame: CGRect, clipped: CGRect, viewport: CGRect) -> Bool {
            !viewport.isNull && !viewport.isInfinite && frame == clipped && frame.width > 1 && frame.height > 1
                && viewport.contains(frame) && frame.minY > viewport.minY && frame.maxY < viewport.maxY
        }
    }

    let frame: CGRect
    let clipped: CGRect
    let exposure: Exposure?
    let verticalClip: VerticalClip?
    let mapping: WindowGeometry.WindowMapping

    static func sample(element: AXUIElement, window: AXUIElement, mapping: WindowGeometry.WindowMapping) throws
        -> NativeElementViewport
    {
        if AXAccess.read(element, kAXEnabledAttribute).value as? Bool == false {
            throw DriverError("the selected pointer element is disabled", code: .inputUnavailable)
        }
        guard let frame = WindowGeometry.hint(element)?.frame, frame.minX.isFinite, frame.minY.isFinite,
            frame.width.isFinite, frame.height.isFinite, frame.width > 0, frame.height > 0
        else { throw DriverError("the selected element has no usable pointer geometry", code: .inputUnavailable) }
        var regions = [(node: AXUIElement, bounds: CGRect, clips: Bool, scrollArea: Bool)]()
        var current = AXAccess.element(element, kAXParentAttribute)
        var seen = [AXUIElement]()
        let deadline = ProcessInfo.processInfo.systemUptime + 0.15
        while let node = current, seen.count < 12, !CFEqual(node, window),
            !seen.contains(where: { CFEqual($0, node) }), ProcessInfo.processInfo.systemUptime < deadline
        {
            try RequestDiagnostics.checkCancellation()
            seen.append(node)
            let role = AXAccess.read(node, kAXRoleAttribute).value as? String
            if [kAXScrollAreaRole, kAXListRole, kAXTableRole, kAXOutlineRole].contains(role ?? ""),
                let bounds = WindowGeometry.hint(node)?.frame, !bounds.isEmpty
            {
                let elementRole = seen.count == 1 && role == kAXTableRole
                    ? AXAccess.read(element, kAXRoleAttribute).value as? String : nil
                regions.append((
                    node, bounds, Self.clips(role: role, distance: seen.count, elementRole: elementRole),
                    role == kAXScrollAreaRole))
            }
            current = AXAccess.element(node, kAXParentAttribute)
        }
        let clipped = regions.filter(\.clips).reduce(frame.intersection(mapping.frame)) { $0.intersection($1.bounds) }
        var exposure: Exposure?
        if clipped.isNull || clipped.isEmpty {
            // If an inner region is itself outside an outer viewport, expose
            // the outer region first. Every subsequent step resamples the same
            // element and the live ancestor chain.
            for (index, region) in regions.enumerated() {
                let viewport = regions.dropFirst(index + 1).filter(\.clips).reduce(
                    region.bounds.intersection(mapping.frame)
                ) {
                    $0.intersection($1.bounds)
                }
                guard !viewport.isNull, !viewport.isEmpty,
                    let movement = movement(frame: frame, viewport: viewport)
                else { continue }
                exposure = Exposure(
                    mapping: mapping, region: region.node, viewport: viewport, frame: frame,
                    direction: movement.direction, pages: movement.pages, distance: movement.distance)
                break
            }
            if exposure == nil, regions.isEmpty, current.map({ CFEqual($0, window) }) == true,
                AXAccess.read(element, "AXHidden").value as? Bool != true
            {
                exposure = try NativeWindowScrollExposure.sample(
                    frame: frame, window: window, mapping: mapping)
            }
        }
        var verticalClip: VerticalClip?
        if frame.height <= 1, exposure == nil {
            for region in regions where region.scrollArea {
                // The provider can already report the clipped sliver. The
                // edge must be a scroll area's own edge, not a window crop.
                let viewport = region.bounds.intersection(mapping.frame)
                if let edge = VerticalClip.edge(frame: frame, clipped: clipped, viewport: viewport),
                    VerticalClip.edge(frame: frame, clipped: clipped, viewport: region.bounds) == edge
                {
                    verticalClip = .init(region: region.node, viewport: region.bounds, edge: edge)
                    break
                }
            }
        }
        return NativeElementViewport(
            frame: frame, clipped: clipped, exposure: exposure, verticalClip: verticalClip, mapping: mapping)
    }

    nonisolated static func clips(role: String?, distance: Int, elementRole: String?) -> Bool {
        role == kAXScrollAreaRole || role == kAXTableRole && distance == 1 && elementRole == kAXRowRole
    }

    nonisolated static func progressed(
        frame: CGRect, clipped: CGRect, window: CGRect, from original: CGRect, originalWindow: CGRect, viewport: CGRect
    ) -> Bool {
        if !clipped.isNull && !clipped.isEmpty { return true }
        let local = frame.offsetBy(dx: -window.minX, dy: -window.minY)
        let originalLocal = original.offsetBy(dx: -originalWindow.minX, dy: -originalWindow.minY)
        let viewportLocal = viewport.offsetBy(dx: -originalWindow.minX, dy: -originalWindow.minY)
        guard local != originalLocal,
            let before = movement(frame: originalLocal, viewport: viewportLocal),
            let after = movement(frame: local, viewport: viewportLocal)
        else { return false }
        return before.direction == after.direction && after.distance < before.distance - 0.5
    }

    /// Pixel geometry only; no item order, labels, time increments or toolkit
    /// assumptions. Limit one wheel to three viewports and measure progress.
    nonisolated static func movement(frame: CGRect, viewport: CGRect)
        -> (direction: String, pages: Double, distance: Double)?
    {
        guard !frame.isEmpty, !viewport.isEmpty, !frame.intersects(viewport) else { return nil }
        let dy = frame.maxY <= viewport.minY || frame.minY >= viewport.maxY ? frame.midY - viewport.midY : 0
        let dx = frame.maxX <= viewport.minX || frame.minX >= viewport.maxX ? frame.midX - viewport.midX : 0
        let vertical = abs(dy) / viewport.height >= abs(dx) / viewport.width
        let delta = vertical ? dy : dx, size = vertical ? viewport.height : viewport.width
        guard delta.isFinite, size.isFinite, size > 0, delta != 0 else { return nil }
        return (
            vertical ? (delta > 0 ? "down" : "up") : (delta > 0 ? "right" : "left"),
            min(3, max(1 / size, abs(delta) / size)), abs(delta))
    }
}
