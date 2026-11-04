// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import Foundation

/// Native names are opaque; descriptions are localized aliases, not commands
/// to translate, normalize, parse or approximate.
enum NativeAction {
    struct Item: Equatable {
        let name: String
        let description: String?
    }

    enum ExplicitResolution: Equatable {
        case selected(Item), absent, ambiguous, namesUnavailable, descriptionsUnavailable, descriptionBudgetExceeded
    }

    /// Resolve the original explicit request before choosing a native command.
    static func resolveExplicit(
        request: String, names: [String]?, description: (String) throws -> String?, check: () throws -> Void = {}
    ) throws -> ExplicitResolution {
        guard let names else { return .namesUnavailable }
        // AXPerformAction addresses a name, not an array slot. Repeated
        // declarations of the same name do not create different commands.
        for name in names {
            try check()
            if name == request { return .selected(Item(name: request, description: nil)) }
        }
        guard let bounded = boundedNames(names) else { return .descriptionBudgetExceeded }
        var matches = [Item](), unknown = false
        for name in bounded {
            try check()
            if let value = try description(name) {
                if value == request { matches.append(Item(name: name, description: value)) }
            } else {
                unknown = true
            }
        }
        if matches.count > 1 { return .ambiguous }
        if unknown { return .descriptionsUnavailable }
        return matches.first.map(ExplicitResolution.selected) ?? .absent
    }

    /// Standard AX action names identify the operation. Their localized
    /// descriptions are presentation, not an additional identity condition.
    /// Custom action aliases retain the description used to select them.
    static func matches(_ actions: [Item]?, name: String, description: String?) -> Bool {
        let standard = [
            "AXPress", "AXPick", "AXConfirm", "AXCancel", "AXIncrement", "AXDecrement", "AXShowMenu", "AXRaise"
        ]
        guard let declared = actions?.filter({ $0.name == name }), declared.count == 1 else { return false }
        return standard.contains(name) || declared[0].description == description
    }

    /// AXPerformAction and AXCopyActionDescription address a name, not an
    /// array entry. Keep one executable declaration per name; raw observation
    /// retains the provider's original array. An incomplete catalogue must not
    /// prevent independently observed pointer/text routes from being queried.
    static func boundedNames(_ names: [String], limit: Int = 128) -> [String]? {
        var seen = Set<String>(), result = [String]()
        for name in names where !name.isEmpty && seen.insert(name).inserted {
            guard result.count < limit else { return nil }
            result.append(name)
        }
        return result
    }

}
