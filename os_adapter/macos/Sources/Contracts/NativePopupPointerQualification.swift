// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices

/// Query-local physical facts, separate from logical observation provenance.
struct NativePopupPointerSample<Node> {
    let geometry: NativeElementPointerUnit.Geometry
    let hit: Node?
    let physicalHit: NativeInputWindowHit
    let foreground: Bool
}

/// Immutable authority for one original element/point in one opaque input
/// contract. A logical owner stamp alone can never construct this proof.
struct NativePopupPointerProof<Node, Process: Equatable> {
    let context: NativePopupOwnerStamp<Node, Process>
    let element: Node
    let pid: pid_t
    let sample: NativePopupPointerSample<Node>

    init?(
        context: NativePopupOwnerStamp<Node, Process>, element: Node, pid: pid_t,
        sample: NativePopupPointerSample<Node>, same: (Node, Node) -> Bool
    ) {
        guard sample.geometry.mapping.id == context.popupWindowID, sample.geometry.mapping.pid == pid,
            Self.physicalWindow(sample.physicalHit, matches: context.popupWindowID), sample.foreground,
            let hit = sample.hit, same(hit, element), Self.usable(sample.geometry)
        else { return nil }
        self.context = context
        self.element = element
        self.pid = pid
        self.sample = sample
    }

    var geometry: NativeElementPointerUnit.Geometry { sample.geometry }

    func matches(_ other: Self, same: (Node, Node) -> Bool) -> Bool {
        context.matches(other.context, same: same) && same(element, other.element) && pid == other.pid
            && geometry.same(as: other.geometry) && geometry.visible == other.geometry.visible
            && geometry.mapping.width == other.geometry.mapping.width
            && geometry.mapping.height == other.geometry.mapping.height
            && sample.physicalHit == other.sample.physicalHit && sample.foreground == other.sample.foreground
    }

    func accepts(_ current: NativePopupPointerSample<Node>, same: (Node, Node) -> Bool) -> Bool {
        guard let proof = Self(context: context, element: element, pid: pid, sample: current, same: same) else {
            return false
        }
        return geometry.same(as: proof.geometry) && geometry.visible == proof.geometry.visible
            && geometry.mapping.width == proof.geometry.mapping.width
            && geometry.mapping.height == proof.geometry.mapping.height
    }

    /// Preserve WindowServer's successful empty/conflicting result. The public
    /// hit is a fallback only when that query is unavailable or failed.
    static func physicalWindow(_ hit: NativeInputWindowHit, matches window: UInt32) -> Bool {
        switch hit.windowServer {
        case .window(let actual): return actual == window
        case .noWindow: return false
        case .unavailable, .failed:
            if case .window(let actual) = hit.accessibility { return actual == window }
            return false
        }
    }

    private static func usable(_ geometry: NativeElementPointerUnit.Geometry) -> Bool {
        let frame = geometry.mapping.frame, element = geometry.frame, point = geometry.point
        let finite = [frame, element, geometry.visible].allSatisfy {
            !$0.isInfinite && !$0.isNull && [$0.minX, $0.minY, $0.width, $0.height].allSatisfy(\.isFinite)
                && $0.width > 0 && $0.height > 0
        }
        return geometry.mapping.pid > 0 && geometry.mapping.width > 0 && geometry.mapping.height > 0 && finite
            && point.x.isFinite && point.y.isFinite && frame.contains(point) && element.contains(point)
            && geometry.visible.contains(point)
    }
}

typealias NativePopupPointerQualification = NativePopupPointerProof<AXUIElement, NativeProcessIdentity>
