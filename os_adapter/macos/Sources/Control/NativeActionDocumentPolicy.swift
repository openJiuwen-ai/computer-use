// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import Foundation

/// Native focus reports and system foreground are independent facts. A
/// confirmed Web action may open a focus-gated modal picker;
/// prepare its exact document, while retaining the original AXPress delivery.
enum NativeActionDocumentPolicy {
    enum WebArea { case confirmed, absent, unknown }
    enum DocumentKind { case standard, other, unknown }
    struct Preparation: Equatable {
        var windowSelectionRequired = false
        var foregroundRequired = false
        var foregroundOnly = false
        var required: Bool { windowSelectionRequired || foregroundOnly }
    }

    static func preparation(
        action: String, exactStandardWindow: Bool, otherDocument: Bool, confirmedWebArea: Bool, foreground: Bool,
        retainedForegroundRequirement: Bool = false
    ) -> Preparation {
        guard action == "AXPress", exactStandardWindow else { return Preparation() }
        let requiresForeground = confirmedWebArea || retainedForegroundRequirement
        return Preparation(
            windowSelectionRequired: otherDocument, foregroundRequired: requiresForeground,
            foregroundOnly: requiresForeground && !foreground)
    }

    static func foregroundEvidence(
        action: String, document: DocumentKind, web: WebArea, foreground: Bool
    ) -> NativeClickContract.DocumentForeground {
        guard action == "AXPress" else { return .notRequired }
        switch document {
        case .other: return .notRequired
        case .unknown: return .unconfirmed(ready: foreground)
        case .standard:
            switch web {
            case .confirmed: return .required(ready: foreground)
            case .absent: return .notRequired
            case .unknown: return .unconfirmed(ready: foreground)
            }
        }
    }

    /// Only a live parent chain reaching the original window proves this
    /// scope. Missing reads, another surface, cycles and a depth limit do not.
    static func classifyWebArea<Element>(
        element: Element, window: Element, same: (Element, Element) -> Bool, role: (Element) throws -> String?,
        parent: (Element) throws -> Element?, check: () throws -> Void
    ) rethrows -> WebArea {
        var current = element, visited = [Element](), web = false
        for _ in 0..<32 {
            try check()
            if same(current, window) { return web ? .confirmed : .absent }
            guard !visited.contains(where: { same($0, current) }), let value = try role(current) else {
                return .unknown
            }
            if ["AXWindow", "AXSheet", "AXDrawer", "AXPopover", "AXApplication"].contains(value) { return .unknown }
            web = web || value == "AXWebArea"
            visited.append(current)
            guard let next = try parent(current) else { return .unknown }
            current = next
        }
        return .unknown
    }

    static func permitsForeground(_ preparation: Preparation, foreground: Bool) -> Bool {
        !preparation.foregroundRequired || foreground
    }
}
