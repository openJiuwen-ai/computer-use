// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import ApplicationServices

/// Native relation acquisition shared by tree reads and explicit continuation.
/// It preserves ordered identities; it does not infer labels, owners or editors.
@MainActor final class AXRelationReader {
    static let tableArrays = [
        "AXRows", "AXVisibleRows", "AXSelectedRows", "AXColumns", "AXVisibleColumns", "AXSelectedColumns",
        "AXVisibleCells", "AXSelectedCells", "AXRowHeaderUIElements", "AXColumnHeaderUIElements", "AXDisclosedRows"
    ]
    static let arrayProperties = Set(
        ["AXChildren", "AXServesAsTitleForUIElements", "AXOwns", "AXLinkedUIElements", "AXSelectedChildren"]
            + tableArrays)
    private static let singleProperties = Set([
        "AXParent", "AXTitleUIElement", "AXWindow", "AXVerticalScrollBar", "AXHorizontalScrollBar", "AXHeader",
        "AXDisclosedByRow"
    ])
    struct API {
        var count: (AXUIElement, String) -> (AXError, Int) = { element, property in
            var count: CFIndex = 0
            let status = AXUIElementGetAttributeValueCount(element, property as CFString, &count)
            return (status, count)
        }
        var range: (AXUIElement, String, Int) -> (AXError, CFArray?) = { element, property, length in
            var values: CFArray?
            let status = AXUIElementCopyAttributeValues(element, property as CFString, 0, length, &values)
            return (status, values)
        }
        var single: (AXUIElement, String) -> (AXError, CFTypeRef?) = { element, property in
            var value: CFTypeRef?
            let status = AXUIElementCopyAttributeValue(element, property as CFString, &value)
            return (status, value)
        }
    }
    struct Page {
        let source: String
        let property: String
        let offset: Int
        let total: Int
        let prefix: [AXUIElement]
        var isArray: Bool? = nil
        var nativeAPI: String? = nil
        var note: String? = nil
        /// A successful getter's payload survives a failed consistency query,
        /// but cannot establish a stable continuation or complete coverage.
        var issue: String? = nil
        var targets: ArraySlice<AXUIElement> { prefix.dropFirst(offset) }
    }
    enum Result {
        case page(Page)
        case failure([String: Any])
    }
    private let pages = NativeRelationPages<AXUIElement>(equal: { CFEqual($0, $1) })
    private let api: API
    init(api: API = API()) { self.api = api }

    func read(
        _ element: AXUIElement, source: String, property: String, limit: Int, cursor: String? = nil,
        withinBudget: () throws -> Bool = { true }
    ) throws -> Result {
        let selected = try pages.select(cursor, source: source, property: property)
        func failure(_ error: AXError, raw: CFTypeRef? = nil) -> Result {
            var result = AXFactEncoding.relationFailure(source: source, property: property, error: error)
            if let raw { result["rawNativeValue"] = raw }
            return .failure(result)
        }
        func timedOut() -> Result { .failure(AXFactEncoding.relationFailure(source: source, property: property)) }
        func page(
            _ prefix: [AXUIElement], total: Int, array: Bool, nativeAPI: String, note: String? = nil,
            issue: String? = nil
        ) throws -> Result {
            try pages.validate(selected, total: total, prefix: prefix)
            return .page(
                Page(
                    source: source, property: property, offset: selected.offset, total: total, prefix: prefix,
                    isArray: array, nativeAPI: nativeAPI, note: note, issue: issue))
        }
        func valuePage(arrayExpected: Bool, note: String? = nil) throws -> Result {
            guard try withinBudget() else { return timedOut() }
            let (status, raw) = api.single(element, property)
            guard status == .success else {
                if case .failure(var value) = failure(status, raw: raw), let note {
                    value["detail"] = note + "; " + (value["detail"] as? String ?? "")
                    return .failure(value)
                }
                return failure(status, raw: raw)
            }
            if let values = raw as? [AXUIElement] {
                let length = min(values.count, selected.offset + max(0, limit), pages.maxPrefix)
                return try page(
                    Array(values.prefix(length)), total: values.count, array: true,
                    nativeAPI: "AXUIElementCopyAttributeValue", note: note)
            }
            guard !arrayExpected, let raw, CFGetTypeID(raw) == AXUIElementGetTypeID() else {
                return failure(.failure, raw: raw)
            }
            return try page(
                limit > 0 ? [unsafeDowncast(raw, to: AXUIElement.self)] : [], total: 1, array: false,
                nativeAPI: "AXUIElementCopyAttributeValue", note: note)
        }
        guard try withinBudget() else { return timedOut() }
        if Self.singleProperties.contains(property) { return try valuePage(arrayExpected: false) }
        let (status, total) = api.count(element, property)
        guard status == .success, total >= 0 else {
            // Unknown attributes can be single-object relations. For known
            // arrays, a metadata-only query must not load an unbounded list.
            if status == .invalidUIElement || (Self.arrayProperties.contains(property) && limit <= 0) {
                return failure(status == .success ? .failure : status)
            }
            return try valuePage(
                arrayExpected: Self.arrayProperties.contains(property),
                note:
                    "AXUIElementGetAttributeValueCount unavailable (AXError \(status.rawValue)); individual getter used"
            )
        }
        try pages.validate(selected, total: total, prefix: selected.prefix)
        let length = min(total, selected.offset + max(0, limit), pages.maxPrefix)
        if length == 0 {
            return try page([], total: total, array: true, nativeAPI: "AXUIElementGetAttributeValueCount")
        }
        guard try withinBudget() else { return timedOut() }
        // Fetch the consumed prefix with the new page so continuation identity
        // checks still detect insertion/reordering rather than skipping nodes.
        let (copied, raw) = api.range(element, property, length)
        guard copied == .success, let values = raw as? [AXUIElement] else {
            if copied == .invalidUIElement { return failure(copied, raw: raw) }
            return try valuePage(
                arrayExpected: true,
                note: "AXUIElementCopyAttributeValues unavailable (AXError \(copied.rawValue)); individual getter used")
        }
        let prefix = Array(values.prefix(length))
        var issue =
            values.count == length ? nil : "native relation returned a different page length; captured prefix only"
        if try withinBudget() {
            let (checked, after) = api.count(element, property)
            if checked != .success || after != total {
                issue = """
                    native relation count changed or became unavailable after reading (AXError \
                    \(checked.rawValue)); captured prefix only; read its source again
                    """
            }
        } else {
            issue = "collection time budget after reading native relation; captured prefix only; read its source again"
        }
        return try page(prefix, total: total, array: true, nativeAPI: "AXUIElementCopyAttributeValues", issue: issue)
    }

    /// Only registered/returned targets are consumed. If a collection budget
    /// ends during registration, a continuation starts at the first omitted one.
    func encode(_ page: Page, targets: [String]) -> [String: Any] {
        precondition(targets.count <= page.targets.count)
        let end = page.offset + targets.count
        var value: [String: Any] = [
            "source": page.source, "property": page.property, "targets": targets, "total": page.total,
            "offset": page.offset, "certainty": "confirmed",
            "state": end == page.total && page.offset == 0 && page.issue == nil ? "complete" : "partial",
            "nativeType": (page.isArray ?? Self.arrayProperties.contains(page.property)) ? "CFArray" : "AXUIElement",
            "nativeAPI": page.nativeAPI
                ?? (Self.arrayProperties.contains(page.property)
                    ? "AXUIElementCopyAttributeValues" : "AXUIElementCopyAttributeValue")
        ]
        if end < page.total {
            if let issue = page.issue {
                value["detail"] = issue
            } else if end == 0 {
                // No native prefix has been consumed. The source and count
                // describe this unexpanded edge; a fresh first page needs no
                // retained objects. Do not evict real continuations for it.
                value["detail"] = "native relation not expanded; read its source to continue"
            } else if let cursor = pages.retain(
                source: page.source, property: page.property, total: page.total, prefix: Array(page.prefix.prefix(end)))
            {
                value["cursor"] = cursor
                value["detail"] = "native relation has an unread continuation"
            } else {
                value["detail"] = "native relation continuation retention budget; unread targets are not captured"
            }
        } else if page.offset > 0 {
            value["detail"] = "continuation page; earlier targets belong to the preceding observation"
        }
        if let issue = page.issue { value["detail"] = issue }
        if let note = page.note {
            value["detail"] = [note, value["detail"] as? String].compactMap { $0 }.joined(separator: "; ")
        }
        return value
    }

    func retainAvailableCursors(_ relations: inout [[String: Any]]) {
        for index in relations.indices {
            if let cursor = relations[index]["cursor"] as? String, !pages.contains(cursor) {
                relations[index].removeValue(forKey: "cursor")
                relations[index]["detail"] = "native relation continuation retention budget; read its source again"
            }
        }
    }
}
