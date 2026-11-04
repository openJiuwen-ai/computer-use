// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import Foundation
import ApplicationServices

/// Qt's Cocoa hit query can visit a lower QWidget before a raised inline list.
/// QWidget child order is stacking order, and Qt's Cocoa unignored-children
/// projection preserves that order while flattening ignored containers. This
/// evidence applies only to that provider; AXChildren has no general z contract.
enum NativeQtInlineListHitEvidence {
    struct Node<Object> {
        let object: Object
        let role: String?
        let identifier: String?
        let pid: Int32?
        let windowID: UInt32?
        let frame: CGRect?
    }
    struct Sibling<Object> {
        let object: Object
        let frame: CGRect?
    }
    struct Facts<Object> {
        let point: CGPoint
        let pid: Int32
        let windowID: UInt32
        let target: Node<Object>
        let list: Node<Object>
        let hit: Node<Object>
        let targetIsLeaf: Bool
        let targetParentIsList: Bool
        let listParentIsWindow: Bool
        let hitParentIsWindow: Bool
        let windowChildren: [Sibling<Object>]
        let listChildren: [Sibling<Object>]
    }

    static func proves<Object>(_ facts: Facts<Object>, same: (Object, Object) -> Bool) -> Bool {
        let target = facts.target, list = facts.list, hit = facts.hit, point = facts.point
        guard facts.pid > 0, facts.windowID != 0, facts.targetIsLeaf,
            facts.targetParentIsList, facts.listParentIsWindow, facts.hitParentIsWindow,
            target.role == kAXStaticTextRole, list.role == kAXListRole,
            NativeQtAccessibilityIdentifier.isWidgetPath(target.identifier),
            NativeQtAccessibilityIdentifier.isWidgetPath(hit.identifier),
            let targetID = target.identifier, let listID = list.identifier, let hitID = hit.identifier,
            targetID.hasPrefix(listID + "."), !listID.hasPrefix(hitID + "."), !hitID.hasPrefix(listID + "."),
            listID != hitID,
            [target, list, hit].allSatisfy({ $0.pid == facts.pid && $0.windowID == facts.windowID }),
            contains(target.frame, point), contains(list.frame, point), contains(hit.frame, point)
        else { return false }
        let listPath = listID.split(separator: "."), hitPath = hitID.split(separator: ".")
        // Distinct flattened QWidget branches still share their real Qt root.
        // A matching application prefix alone cannot establish that relation.
        guard zip(listPath, hitPath).prefix(while: { $0 == $1 }).count >= 2 else { return false }
        let lists = facts.windowChildren.indices.filter { same(facts.windowChildren[$0].object, list.object) }
        let hits = facts.windowChildren.indices.filter { same(facts.windowChildren[$0].object, hit.object) }
        guard lists.count == 1, hits.count == 1, let listIndex = lists.first, let hitIndex = hits.first,
            listIndex > hitIndex,
            facts.listChildren.filter({ same($0.object, target.object) }).count == 1
        else { return false }
        // A later QWidget is above this list. Unknown geometry cannot prove
        // non-coverage, and another row at this point cannot prove this leaf.
        for sibling in facts.windowChildren.dropFirst(listIndex + 1) {
            guard excludes(sibling.frame, point) else { return false }
        }
        for sibling in facts.listChildren where !same(sibling.object, target.object) {
            guard excludes(sibling.frame, point) else { return false }
        }
        return true
    }

    private static func finite(_ frame: CGRect?) -> CGRect? {
        guard let frame, !frame.isNull, !frame.isInfinite,
            [frame.minX, frame.minY, frame.width, frame.height].allSatisfy(\.isFinite)
        else { return nil }
        return frame
    }
    private static func contains(_ frame: CGRect?, _ point: CGPoint) -> Bool {
        guard let frame = finite(frame), !frame.isEmpty else { return false }
        return frame.contains(point)
    }
    private static func excludes(_ frame: CGRect?, _ point: CGPoint) -> Bool {
        guard let frame = finite(frame) else { return false }
        return !frame.contains(point)
    }

    /// QUERY: every sample uses current objects, children, frames and native
    /// window IDs. No observation cache, action, focus change or input occurs.
    @MainActor static func sample(
        target: AXUIElement, hit: AXUIElement, window: AXUIElement, pid: pid_t, windowID: CGWindowID,
        at point: CGPoint
    ) throws -> Bool {
        guard let list = AXAccess.element(target, kAXParentAttribute),
            AXAccess.read(list, kAXRoleAttribute).value as? String == kAXListRole,
            NativeQtAccessibilityIdentifier.isWidgetPath(AXAccess.read(target, kAXIdentifierAttribute).value as? String),
            NativeQtAccessibilityIdentifier.isWidgetPath(AXAccess.read(hit, kAXIdentifierAttribute).value as? String),
            let windowChildren = AXAccess.read(window, kAXChildrenAttribute).value as? [AXUIElement],
            let listIndex = windowChildren.firstIndex(where: { CFEqual($0, list) }),
            let listChildren = AXAccess.read(list, kAXChildrenAttribute).value as? [AXUIElement],
            let targetChildren = AXAccess.read(target, kAXChildrenAttribute).value as? [AXUIElement]
        else { return false }
        try RequestDiagnostics.checkCancellation()
        func node(_ object: AXUIElement) -> Node<AXUIElement> {
            var owner: pid_t = 0
            return .init(
                object: object, role: AXAccess.read(object, kAXRoleAttribute).value as? String,
                identifier: AXAccess.read(object, kAXIdentifierAttribute).value as? String,
                pid: AXUIElementGetPid(object, &owner) == .success ? owner : nil,
                windowID: BackgroundEvents.nativeWindowID(object), frame: WindowGeometry.hint(object)?.frame)
        }
        func sibling(_ object: AXUIElement) -> Sibling<AXUIElement> {
            .init(object: object, frame: WindowGeometry.hint(object)?.frame)
        }
        func parentIsWindow(_ object: AXUIElement) -> Bool {
            AXAccess.element(object, kAXParentAttribute).map { CFEqual($0, window) } == true
        }
        let facts = Facts(
            point: point, pid: pid, windowID: windowID, target: node(target), list: node(list), hit: node(hit),
            targetIsLeaf: targetChildren.isEmpty, targetParentIsList: true,
            listParentIsWindow: parentIsWindow(list), hitParentIsWindow: parentIsWindow(hit),
            // Lower branches only contribute ordering, so do not fetch the
            // geometry of the whole form for each pointer event.
            windowChildren: windowChildren.enumerated().map { index, object in
                index > listIndex ? sibling(object) : .init(object: object, frame: nil)
            }, listChildren: listChildren.map(sibling))
        try RequestDiagnostics.checkCancellation()
        return proves(facts, same: { CFEqual($0, $1) })
    }
}
