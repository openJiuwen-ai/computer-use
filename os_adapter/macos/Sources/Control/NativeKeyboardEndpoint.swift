// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices

/// Resolve an event-capable process through the focused object's real parent
/// chain. Public AX ownership, the hosting application and this endpoint are
/// separate facts. The caller proves the original window's containment.
enum NativeKeyboardEndpoint {
    struct Candidate<Element> {
        let element: Element
        let pid: pid_t
        let kind: RemoteApplicationKind
    }

    static func find<Element>(
        focus: Element, limit: Int = 32, same: (Element, Element) -> Bool,
        pid: (Element) -> pid_t?, kind: (pid_t) -> RemoteApplicationKind,
        parent: (Element) -> Element?, check: () throws -> Void = {}
    ) throws -> Candidate<Element>? {
        var current: Element? = focus
        var seen = [Element]()
        while let element = current, seen.count < limit {
            try check()
            guard !seen.contains(where: { same($0, element) }) else { return nil }
            seen.append(element)
            if let actual = pid(element), actual > 0 {
                let category = kind(actual)
                if category != .unknown { return Candidate(element: element, pid: actual, kind: category) }
            }
            current = parent(element)
        }
        return nil
    }

    @MainActor static func read(_ focus: AXUIElement) throws -> Candidate<AXUIElement>? {
        try find(
            focus: focus, same: { CFEqual($0, $1) }, pid: BackgroundEvents.accessibilityProcessID,
            kind: RemoteApplicationKind.read,
            parent: { AXAccess.element($0, kAXParentAttribute) },
            check: RequestDiagnostics.checkCancellation)
    }
}
