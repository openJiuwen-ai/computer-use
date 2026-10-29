// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import Foundation
import CoreGraphics
import Darwin

/// AXApplication children can still be attached native windows. Supplement
/// AX containment only with exact WindowServer identities, never titles/size.
@MainActor enum NativeWindowParents {
    private typealias Connection = @convention(c) () -> Int32
    private typealias Query = @convention(c) (Int32, CFArray, Int32) -> Unmanaged<CFTypeRef>?
    private typealias CopyWindows = @convention(c) (CFTypeRef) -> Unmanaged<CFTypeRef>?
    private typealias Count = @convention(c) (CFTypeRef) -> Int32
    private typealias Advance = @convention(c) (CFTypeRef) -> Bool
    private typealias Identifier = @convention(c) (CFTypeRef) -> CGWindowID
    private static let library = dlopen(
        "/System/Library/PrivateFrameworks/SkyLight.framework/SkyLight", RTLD_LAZY | RTLD_LOCAL)
    private static func symbol<T>(_ name: String, as: T.Type) -> T? {
        library.flatMap { dlsym($0, name) }.map { unsafeBitCast($0, to: T.self) }
    }
    private static let connection = symbol("SLSMainConnectionID", as: Connection.self)
    private static let query = symbol("SLSWindowQueryWindows", as: Query.self)
    private static let copyWindows = symbol("SLSWindowQueryResultCopyWindows", as: CopyWindows.self)
    private static let count = symbol("SLSWindowIteratorGetCount", as: Count.self)
    private static let advance = symbol("SLSWindowIteratorAdvance", as: Advance.self)
    private static let windowID = symbol("SLSWindowIteratorGetWindowID", as: Identifier.self)
    private static let parentID = symbol("SLSWindowIteratorGetParentID", as: Identifier.self)

    /// Optional read-only SPI, also used by yabai. Missing support/query rows
    /// leave AX discovery intact. Zero reports no native attachment; it must
    /// not erase a logical AX sheet/popover relationship.
    static func read(_ ids: [CGWindowID]) -> [CGWindowID: CGWindowID]? {
        let ids = Set(ids)
        guard !ids.isEmpty else { return [:] }
        guard ids.count <= Int(Int32.max), let connection, let query, let copyWindows, let count, let advance,
            let windowID, let parentID,
            let result = query(connection(), ids.map { NSNumber(value: $0) } as CFArray, Int32(ids.count))?
                .takeRetainedValue(), let iterator = copyWindows(result)?.takeRetainedValue()
        else { return nil }
        return withExtendedLifetime(result) {
            let total = Int(count(iterator))
            guard total >= 0, total <= ids.count else { return nil }
            var parents = [CGWindowID: CGWindowID]()
            for _ in 0..<total {
                guard advance(iterator) else { return nil }
                let id = windowID(iterator)
                guard ids.contains(id), parents[id] == nil else { return nil }
                parents[id] = parentID(iterator)
            }
            return parents
        }
    }

    struct Window {
        let ref: String
        let pid: pid_t
        let id: CGWindowID?
        let parent: String?
    }
    struct Result {
        var parents: [String: String]
        var unresolved = Set<String>()
    }

    /// Preserve AX evidence. Conflicting/missing/cyclic native relationships
    /// are uncertainty, not permission to pick or operate another document.
    static func supplement(_ windows: [Window], native: [CGWindowID: CGWindowID]?) -> Result {
        var result = Result(
            parents: Dictionary(
                windows.compactMap { w in w.parent.map { (w.ref, $0) } }, uniquingKeysWith: { first, _ in first }))
        guard let native else { return result }
        let byID = Dictionary(grouping: windows.filter { $0.id != nil }, by: { $0.id! })
        var added = Set<String>()
        for window in windows {
            guard let id = window.id, let parentID = native[id], parentID != 0 else { continue }
            guard byID[id]?.count == 1, let candidates = byID[parentID], candidates.count == 1,
                let parent = candidates.first, parent.pid == window.pid, parent.ref != window.ref,
                window.parent == nil || window.parent == parent.ref
            else {
                result.unresolved.insert(window.ref)
                continue
            }
            if window.parent == nil {
                result.parents[window.ref] = parent.ref
                added.insert(window.ref)
            }
        }
        // Evaluate the combined graph before removing anything, so every new
        // edge into a cycle is rejected independently of dictionary order.
        let proposed = result.parents
        for ref in added {
            var current: String? = ref, visited = Set<String>()
            while let node = current {
                guard visited.insert(node).inserted else {
                    result.unresolved.insert(ref)
                    break
                }
                current = proposed[node]
            }
        }
        for ref in added.intersection(result.unresolved) { result.parents.removeValue(forKey: ref) }
        return result
    }
}
