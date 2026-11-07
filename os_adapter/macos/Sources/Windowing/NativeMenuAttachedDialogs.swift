// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices

/// Extra nonmodal dialogs are not independent menu receivers when their
/// current ownership leads to the exact main and focused document.
@MainActor struct NativeMenuAttachedDialogs {
    private let elements: [AXUIElement]
    private let ids: [CGWindowID]
    private let process: NativeProcessIdentity

    static func candidate(root: AXUIElement, evidence: NativeInputScope.WindowEvidence?, pid: pid_t) -> Self? {
        guard let evidence, let native = evidence.nativeWindows,
            (2...9).contains(evidence.windows.count),
            let original = evidence.windows.first(where: { CFEqual($0.element, root) })
        else { return nil }
        let others = evidence.windows.filter { !CFEqual($0.element, root) }
        guard others.count + 1 == evidence.windows.count,
            others.allSatisfy({ $0.kind == "dialog" && $0.modal == false && $0.document == nil && !$0.parentUnknown })
        else { return nil }
        let windows = [original] + others
        let ids = windows.compactMap(\.nativeID)
        guard ids.count == windows.count, !ids.contains(0), Set(ids).count == ids.count,
            let process = native.first(where: { $0.mapping.id == ids[0] })?.process, process.pid == pid,
            ids.allSatisfy({ id in
                let matches = native.filter { $0.mapping.id == id }
                return matches.count == 1 && matches[0].process == process && matches[0].menu == nil
            }), native.allSatisfy({ $0.menu != nil || ids.contains($0.mapping.id) })
        else { return nil }
        return .init(elements: windows.map(\.element), ids: ids, process: process)
    }

    func confirm(_ windows: [AXUIElement], application: AXUIElement) throws -> Bool {
        guard windows.count <= 32, windows.allSatisfy({ window in elements.contains { CFEqual($0, window) } }),
            elements.allSatisfy({ element in windows.contains { CFEqual($0, element) } }),
            NativeProcessIdentity.read(process.pid) == process
        else { return false }
        let deadline = ProcessInfo.processInfo.systemUptime + 0.25
        var axParents = [CGWindowID: CGWindowID]()
        for (index, element) in elements.enumerated() {
            try RequestDiagnostics.checkCancellation()
            guard ProcessInfo.processInfo.systemUptime < deadline else { return false }
            let id = ids[index]
            var owner: pid_t = 0
            guard AXUIElementGetPid(element, &owner) == .success, owner == process.pid,
                BackgroundEvents.nativeWindowID(element) == id,
                let window = WindowServerInfo.window(id),
                (window[kCGWindowOwnerPID as String] as? NSNumber)?.int32Value == process.pid
            else { return false }
            guard index != 0 else { continue }
            guard AXAccess.read(element, kAXSubroleAttribute).value as? String == kAXDialogSubrole,
                let modal = AXAccess.read(element, kAXModalAttribute).value,
                CFGetTypeID(modal) == CFBooleanGetTypeID(), modal as? Bool == false
            else { return false }
            switch AXAccess.read(element, kAXParentAttribute) {
            case .unsupported, .unavailable(.noValue): break
            case .value(let value) where CFGetTypeID(value) == AXUIElementGetTypeID():
                let parent = unsafeDowncast(value, to: AXUIElement.self)
                if !CFEqual(parent, application) {
                    guard let parentIndex = elements.firstIndex(where: { CFEqual($0, parent) }) else { return false }
                    axParents[id] = ids[parentIndex]
                }
            default: return false
            }
        }
        try RequestDiagnostics.checkCancellation()
        guard ProcessInfo.processInfo.systemUptime < deadline else { return false }
        let nativeParents = NativeWindowParents.read(ids) ?? [:]
        try RequestDiagnostics.checkCancellation()
        return ProcessInfo.processInfo.systemUptime < deadline && NativeProcessIdentity.read(process.pid) == process
            && Self.provesAttachment(ids: ids, axParents: axParents, nativeParents: nativeParents)
    }

    static func provesAttachment(
        ids: [CGWindowID], axParents: [CGWindowID: CGWindowID], nativeParents: [CGWindowID: CGWindowID]
    ) -> Bool {
        guard let root = ids.first, ids.count > 1 else { return false }
        let allowed = Set(ids)
        var parents = [CGWindowID: CGWindowID]()
        for child in ids.dropFirst() {
            let ax = axParents[child], native = nativeParents[child].flatMap { $0 == 0 ? nil : $0 }
            guard ax == nil || native == nil || ax == native,
                let parent = ax ?? native, allowed.contains(parent), child != parent
            else { return false }
            parents[child] = parent
        }
        return ids.dropFirst().allSatisfy { child in
            var current = child, seen = Set<CGWindowID>()
            while current != root {
                guard seen.insert(current).inserted, let parent = parents[current] else { return false }
                current = parent
            }
            return true
        }
    }
}
