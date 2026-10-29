// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import Foundation

/// Final input boundary for an already selected surface. Unlike a read-view
/// selector, this neither picks a modal nor treats the root as the only input
/// surface. Incomplete AX data cannot establish a child relationship.
enum NativeWindowBoundary {
    /// A node read through a parent tree can retain a nearer native sheet.
    /// The caller has matched that original object to the live catalogue.
    /// Only narrow the observed range; a missing, unrelated or outer boundary
    /// never selects a new window. Node-free input supplies no candidate.
    static func interactionWindow(observed: String, candidate: String?, windows: [AppSelection.Window]) -> String {
        guard let candidate else { return observed }
        var current: String? = candidate, seen = Set<String>()
        while let ref = current, seen.insert(ref).inserted {
            if ref == observed { return candidate }
            current = windows.first { $0.ref == ref }?.parent
        }
        return observed
    }

    /// A confirmed modal descendant is the current interaction range even
    /// when the public App remains bound to its original document. Prune only
    /// after proving that live relationship; ordinary panels and unknown or
    /// contradictory parent chains keep discovery rooted at the document.
    static func contentRoot<Element>(
        workingRoot: Element, target: Element, same: (Element, Element) -> Bool, modal: (Element) throws -> Bool?,
        parent: (Element) throws -> Element?
    ) rethrows -> Element {
        guard !same(workingRoot, target), try modal(target) == true else { return workingRoot }
        var current = target, seen = [Element]()
        for _ in 0..<32 {
            if same(current, workingRoot) { return target }
            guard !seen.contains(where: { same($0, current) }), let next = try parent(current) else {
                return workingRoot
            }
            seen.append(current)
            current = next
        }
        return workingRoot
    }

    /// Content-discovered surfaces belong to the window whose child edges
    /// expose them. While validating input, only a confirmed ancestor path
    /// ending at this application can exclude another window's contents.
    /// Unknown/recycled/cyclic ancestry keeps the full discovery path.
    static func exploreContents<Element>(
        of window: Element, root: Element, same: (Element, Element) -> Bool, parent: (Element) throws -> Element?,
        isApplication: (Element) throws -> Bool
    ) rethrows -> Bool {
        var node = window, seen = [Element]()
        for _ in 0..<32 {
            if same(node, root) { return true }
            if try isApplication(node) { return false }
            if seen.contains(where: { same($0, node) }) { return true }
            seen.append(node)
            guard let next = try parent(node) else { return true }
            node = next
        }
        return true
    }

    static func blocked(
        root: String, target: String, windows: [AppSelection.Window], focused: @autoclosure () -> String? = nil
    ) throws -> [String] {
        var graph = [String: AppSelection.Window]()
        for window in windows {
            guard graph.updateValue(window, forKey: window.ref) == nil else {
                throw DriverError("native window relationship is ambiguous", code: .inputUnavailable)
            }
        }
        func ancestry(_ ref: String) -> Set<String> {
            var current: String? = ref, seen = Set<String>()
            while let ref = current, seen.insert(ref).inserted { current = graph[ref]?.parent }
            return seen
        }
        let targetPath = ancestry(target)
        guard targetPath.contains(root) else {
            throw DriverError("input surface left its selected working-window range", code: .staleTarget)
        }
        // Nested standalone modal loops can expose two AXModal windows whose
        // AXParent is the application. The current focused modal is operable;
        // the older ownerless modal is not its blocker. This fresh native
        // focus report establishes neither ownership nor keyboard readiness.
        // Ordinary document input needs no additional native focus query.
        let inFocusedModal =
            targetPath.contains { graph[$0]?.modal == true }
            && (focused().map { targetPath.contains($0) && graph[$0]?.modal == true } ?? false)
        return windows.filter { window in
            guard window.modal, !targetPath.contains(window.ref) else { return false }
            // A known sheet in this document, or a reported app-modal window
            // without an owner, blocks input. Confirmed child/sibling modals
            // still block even when a stale focus report favors the target.
            // Unknown focus never resolves ambiguous standalone modals.
            return ancestry(window.ref).contains(root)
                || (!inFocusedModal && (window.parent.map { graph[$0] == nil } ?? true))
        }.map(\.ref).sorted()
    }
}
