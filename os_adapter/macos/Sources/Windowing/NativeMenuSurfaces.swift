// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices

/// An application's visible menu is an interaction surface, not a document.
/// Require native menu semantics AND a unique same-process WindowServer match.
/// A popup layer alone never removes an unknown/custom window from discovery.
@MainActor enum NativeMenuSurfaces {
    static func visibleChildCount(_ menu: AXUIElement, pid: pid_t) -> Int? {
        var owner: pid_t = 0, count: CFIndex = 0
        guard AXUIElementGetPid(menu, &owner) == .success, owner == pid,
            AXAccess.read(menu, kAXRoleAttribute).value as? String == kAXMenuRole,
            AXUIElementGetAttributeValueCount(menu, kAXVisibleChildrenAttribute as CFString, &count) == .success,
            count >= 0
        else { return nil }
        return count
    }

    static func frame(_ menu: AXUIElement, pid: pid_t) -> CGRect? {
        guard let count = visibleChildCount(menu, pid: pid), count > 0,
            let frame = WindowGeometry.hint(menu)?.frame, frame.width > 1, frame.height > 1
        else { return nil }
        return frame
    }

    /// AXMenu can also describe a persistent inline list. Only a visible,
    /// uniquely matching native popup surface proves that keyboard input must
    /// preserve menu tracking instead of preparing the document normally.
    static func isPresentedPopup(_ menu: AXUIElement, pid: pid_t) -> Bool {
        mapping(menu, pid: pid) != nil
    }

    /// Reuse the unique visible popup proof as the addressed event mapping.
    static func mapping(_ menu: AXUIElement, pid: pid_t) -> WindowGeometry.WindowMapping? {
        guard let frame = frame(menu, pid: pid),
            let windows = CGWindowListCopyWindowInfo(.optionOnScreenOnly, kCGNullWindowID) as? [[String: Any]]
        else { return nil }
        let matches = windows.compactMap { info -> WindowGeometry.WindowMapping? in
            guard (info[kCGWindowOwnerPID as String] as? NSNumber)?.int32Value == pid,
                (info[kCGWindowLayer as String] as? NSNumber)?.intValue == Int(CGWindowLevelForKey(.popUpMenuWindow)),
                let id = (info[kCGWindowNumber as String] as? NSNumber)?.uint32Value, id != 0,
                let raw = info[kCGWindowBounds as String] as? [String: Any],
                let bounds = CGRect(dictionaryRepresentation: raw as CFDictionary),
                WindowSelection.sameFrame(frame, bounds),
                let width = Int(exactly: bounds.width.rounded()), let height = Int(exactly: bounds.height.rounded())
            else { return nil }
            return .init(id: id, pid: pid, frame: bounds, title: nil, width: width, height: height)
        }
        return matches.count == 1 ? matches.first : nil
    }

    static func matches(_ menu: AXUIElement, window: WindowGeometry.WindowMapping) -> Bool {
        guard let frame = frame(menu, pid: window.pid), WindowSelection.sameFrame(frame, window.frame) else { return false }
        return matchesPhysicalWindow(window,
            rows: CGWindowListCopyWindowInfo(.optionOnScreenOnly, kCGNullWindowID) as? [[String: Any]])
    }

    /// AXMenu's owning window ID may name its document, not its rendering
    /// popup. Match one live physical popup; never use that owning ID here.
    /// Production rows come from the onscreen WindowServer projection.
    static func matchesPhysicalWindow(_ window: WindowGeometry.WindowMapping, rows: [[String: Any]]?) -> Bool {
        guard let rows else { return false }
        let matches = rows.filter { row in
            guard (row[kCGWindowOwnerPID as String] as? NSNumber)?.int32Value == window.pid,
                (row[kCGWindowIsOnscreen as String] as? NSNumber)?.boolValue != false,
                (row[kCGWindowLayer as String] as? NSNumber)?.intValue == Int(CGWindowLevelForKey(.popUpMenuWindow)),
                let raw = row[kCGWindowBounds as String] as? [String: Any],
                let frame = CGRect(dictionaryRepresentation: raw as CFDictionary)
            else { return false }
            return WindowSelection.sameFrame(frame, window.frame)
        }
        return matches.count == 1
            && (matches.first?[kCGWindowNumber as String] as? NSNumber)?.uint32Value == window.id
    }

    /// Read-only native discovery port. Queries never prepare focus or input.
    @MainActor struct Query {
        var trusted: () -> Bool = { AXIsProcessTrusted() }
        var application: (pid_t) -> AXUIElement = { AXUIElementCreateApplication($0) }
        var configure: (AXUIElement) -> Void = { _ = AXUIElementSetMessagingTimeout($0, 0.05) }
        var menuBar: (AXUIElement) -> AXUIElement? = { AXAccess.element($0, kAXMenuBarAttribute) }
        var role: (AXUIElement) -> String? = { AXAccess.read($0, kAXRoleAttribute).value as? String }
        var children: (AXUIElement) -> [AXUIElement]? = { AXAccess.elements($0, kAXChildrenAttribute, limit: 256) }
        var parent: (AXUIElement) -> AXUIElement? = { AXAccess.element($0, kAXParentAttribute) }
        var nativeID: (AXUIElement) -> CGWindowID? = { BackgroundEvents.nativeWindowID($0) }
        var frame: (AXUIElement, pid_t) -> CGRect? = { NativeMenuSurfaces.frame($0, pid: $1) }
        var hit: (AXUIElement, CGPoint) -> AXUIElement? = { root, point in
            var element: AXUIElement?
            return AXUIElementCopyElementAtPosition(root, Float(point.x), Float(point.y), &element) == .success ? element : nil
        }
        var now: () -> Double = { ProcessInfo.processInfo.systemUptime }
    }

    static func classify(_ windows: [NativeWindows.Window], query: Query = Query()) -> [NativeWindows.Window] {
        guard query.trusted() else { return windows }
        var result = windows
        for pid in Set(windows.filter { $0.level == Int(CGWindowLevelForKey(.popUpMenuWindow)) }.map { $0.process.pid })
        {
            let deadline = query.now() + 0.15
            let root = query.application(pid)
            query.configure(root)
            var pending: [(element: AXUIElement, parent: AXUIElement?)] =
                query.menuBar(root).map { [($0, nil)] } ?? []
            var seen = [AXUIElement](), menus = [(element: AXUIElement, frame: CGRect, parent: AXUIElement?)]()
            var cursor = 0
            while cursor < pending.count, seen.count < 256, query.now() < deadline {
                let item = pending[cursor], element = item.element
                cursor += 1
                guard !seen.contains(where: { CFEqual($0, element) }) else { continue }
                seen.append(element)
                query.configure(element)
                let role = query.role(element)
                if role == kAXMenuRole {
                    // Hidden static menus are not live input surfaces. Do not
                    // expand their potentially large, unrelated command trees.
                    guard let bounds = query.frame(element, pid) else { continue }
                    menus.append((element, bounds, item.parent))
                }
                pending += (query.children(element) ?? []).map {
                    ($0, role == kAXMenuRole ? element : item.parent)
                }
            }
            if cursor != pending.count { menus.removeAll() }
            // Control menus need not be descendants of AXMenuBar or AXWindows.
            // Probe only otherwise-unassociated physical popup surfaces, then
            // follow their actual AX parent chain. No document tree is scanned.
            let popupDeadline = query.now() + 0.15
            var hosts = [CFHashCode: CGWindowID]()
            for window in result where window.process.pid == pid
                && window.level == Int(CGWindowLevelForKey(.popUpMenuWindow)) && window.menu == nil
            {
                guard query.now() < popupDeadline,
                    !menus.contains(where: { WindowSelection.sameFrame($0.frame, window.mapping.frame) }),
                    windows.filter({ $0.process.pid == pid && $0.level == window.level
                        && WindowSelection.sameFrame($0.mapping.frame, window.mapping.frame) }).count == 1
                else { continue }
                let point = CGPoint(x: window.mapping.frame.midX, y: window.mapping.frame.midY)
                var current = query.hit(root, point), ancestry = [AXUIElement]()
                var selected: (element: AXUIElement, frame: CGRect, parent: AXUIElement?)?
                while let node = current, ancestry.count < 12, query.now() < popupDeadline,
                    !ancestry.contains(where: { CFEqual($0, node) })
                {
                    ancestry.append(node)
                    query.configure(node)
                    let role = query.role(node)
                    if role == kAXMenuRole {
                        if selected != nil { selected?.parent = node; break }
                        guard let bounds = query.frame(node, pid),
                            WindowSelection.sameFrame(bounds, window.mapping.frame) else { break }
                        selected = (node, bounds, nil)
                    }
                    if [kAXWindowRole, kAXSheetRole].contains(role ?? "") {
                        if let selected, let id = query.nativeID(node),
                            windows.filter({ $0.process.pid == pid && $0.mapping.id == id }).count == 1,
                            id != window.mapping.id { hosts[CFHash(selected.element)] = id }
                        break
                    }
                    if role == kAXApplicationRole { break }
                    current = query.parent(node)
                }
                if let selected, !menus.contains(where: { CFEqual($0.element, selected.element) }) {
                    menus.append(selected)
                }
            }
            for index in result.indices
            where result[index].process.pid == pid && result[index].level == Int(CGWindowLevelForKey(.popUpMenuWindow))
            {
                let window = result[index]
                let candidates = menus.filter { WindowSelection.sameFrame($0.1, window.mapping.frame) }
                guard candidates.count == 1, let menu = candidates.first,
                    windows.filter({
                        $0.process.pid == pid && $0.level == window.level
                            && WindowSelection.sameFrame($0.mapping.frame, menu.1)
                    }).count == 1
                else { continue }
                result[index].menu = menu.0
                result[index].menuHost = hosts[CFHash(menu.0)]
            }
            for index in result.indices where result[index].process.pid == pid {
                guard let menu = result[index].menu,
                    let parent = menus.first(where: { CFEqual($0.element, menu) })?.parent
                else { continue }
                let owners = result.filter { $0.process.pid == pid && $0.menu.map { CFEqual($0, parent) } == true }
                if owners.count == 1 { result[index].menuParent = owners[0].mapping.id }
            }
        }
        return result
    }

    /// Only one complete native menu chain may select its innermost popup.
    /// Unrelated/unknown surfaces remain explicit choices, with all pixels kept.
    static func current(_ windows: [NativeWindows.Window]) -> CGWindowID? {
        guard !windows.isEmpty, windows.allSatisfy({ $0.menu != nil }) else { return nil }
        let roots = windows.filter { $0.menuParent == nil }
        guard roots.count == 1 else { return nil }
        var current = roots[0].mapping.id, visited = Set<CGWindowID>()
        while visited.insert(current).inserted {
            let children = windows.filter { $0.menuParent == current }
            if children.isEmpty { return visited.count == windows.count ? current : nil }
            guard children.count == 1 else { return nil }
            current = children[0].mapping.id
        }
        return nil
    }
}
