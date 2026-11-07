// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import Foundation

/// Native constraints on the retained object in its original working scope.
/// Display text, values, selection and enabled state may change on that object;
/// its reference does not promise an immutable, unobservable business meaning.
struct ElementIdentity: Equatable {
    struct Facts {
        var values: [String: String] = [:]
        var absent: Set<String> = []
    }
    enum Check { case matched, unavailable, changed }
    let attributes: [String: String]
    let absent: Set<String>

    // A retained provider object can be recycled for another virtual cell.
    // These are native, advertised positions, not labels or a parsed A1 name.
    static let positionKeys: Set<String> = ["AXRowIndexRange", "AXColumnIndexRange"]
    static let keys =
        ["AXRole", "AXSubrole", "AXIdentifier", "AXURL", "AXDocument"]
        + positionKeys.sorted()

    init(facts: Facts) {
        let keys = Set(Self.keys)
        self.init(
            attributes: facts.values.filter { keys.contains($0.key) && !$0.value.isEmpty },
            absent: facts.absent.union(facts.values.filter { $0.value.isEmpty }.map(\.key)).intersection(keys))
    }

    private init(attributes: [String: String], absent: Set<String>) {
        var keys = Set(Self.keys)
        // An identified native row may move when neighbours are inserted or
        // sorted. Its advertised ordinal is not another identity requirement.
        // Unidentified virtual rows and positional cells retain position checks.
        if attributes["AXRole"] == "AXRow", let identifier = attributes["AXIdentifier"], !identifier.isEmpty {
            keys.subtract(Self.positionKeys)
        }
        self.attributes = attributes.filter { keys.contains($0.key) }
        self.absent = absent.intersection(keys)
    }

    /// Missing evidence does not contradict the old identity. Preserve known
    /// constraints while permitting later reads to refine previously unknown fields.
    func refining(with next: Self) -> Self? {
        guard
            attributes.allSatisfy({ key, value in
                !next.absent.contains(key) && (next.attributes[key] == nil || next.attributes[key] == value)
            })
        else { return nil }
        let merged = attributes.merging(next.attributes) { _, new in new }
        // Absence can contradict a previously observed value, but is not a
        // permanent identity constraint. A provider may expose an optional ID
        // later; failure to query a never-present ID must not block all input.
        return Self(attributes: merged, absent: absent.union(next.absent).subtracting(merged.keys))
    }

    /// Check current facts, never merged history. A failed query cannot prove
    /// success, and cannot permanently retire an otherwise retained object.
    func check(_ current: Facts) -> Check {
        if attributes.contains(where: { key, value in
            current.absent.contains(key) || current.values[key].map { $0 != value } == true
        }) {
            return .changed
        }
        guard attributes["AXRole"] != nil, attributes.allSatisfy({ current.values[$0.key] == $0.value }) else {
            return .unavailable
        }
        return .matched
    }

    func matches(_ current: [String: String]) -> Bool { check(Facts(values: current)) == .matched }

    /// An optional native document witness may resolve exactly one reported
    /// location change. Other attributes, unknown fields and absent URLs keep
    /// their ordinary identity semantics.
    func locationChange(_ current: Facts) -> Bool {
        guard attributes["AXRole"] == "AXWebArea", let url = attributes["AXURL"], let latest = current.values["AXURL"],
            !latest.isEmpty, latest != url
        else { return false }
        var fixed = current
        fixed.values["AXURL"] = url
        return check(fixed) == .matched
    }

    func preservingLocation(from previous: Self) -> Self? {
        guard previous.locationChange(.init(values: attributes, absent: absent)), let url = previous.attributes["AXURL"]
        else { return nil }
        var fixed = attributes
        fixed["AXURL"] = url
        return Self(attributes: fixed, absent: absent)
    }

    /// Ancestor metadata is not automatically a descendant's identity. A
    /// layout group's identifier/URL, or a window's subrole/identifier, can
    /// change while the exact child remains alive. Keep only actual ownership
    /// boundaries here; NativeScopeResolver independently checks the fixed
    /// window lifecycle and current input membership before dispatch.
    func inheritedBoundary(inMenu: Bool) -> Self? {
        let role = attributes["AXRole"] ?? ""
        let keys: Set<String>
        if ["AXWindow", "AXSheet", "AXPopover"].contains(role) {
            keys = ["AXRole", "AXDocument"]
        } else if role == "AXRow" || !Set(attributes.keys).isDisjoint(with: Self.positionKeys) {
            // A reused row/cell can keep its AX address while acquiring a
            // different identifier or advertised position.
            keys = Set(Self.keys)
        } else if role == "AXMenuBar" || inMenu && ["AXMenu", "AXMenuItem", "AXMenuBarItem"].contains(role) {
            keys = ["AXRole", "AXIdentifier"]
        } else if attributes["AXDocument"] != nil || role == "AXWebArea" && attributes["AXURL"] != nil {
            keys = Set(["AXRole", "AXDocument"])
                .union(role == "AXWebArea" ? ["AXURL"] : [])
        } else {
            return nil
        }
        return Self(attributes: attributes.filter { keys.contains($0.key) }, absent: absent.intersection(keys))
    }

    /// Field names suffice to explain a rejection without logging document
    /// URLs, identifiers or other application content.
    func changedFields(in current: Facts) -> [String] {
        attributes.keys.filter { key in
            current.absent.contains(key) || current.values[key].map { $0 != attributes[key] } == true
        }.sorted()
    }

    func unavailableFields(in current: Facts) -> [String] {
        attributes.keys.filter { current.values[$0] == nil && !current.absent.contains($0) }.sorted()
    }
}
