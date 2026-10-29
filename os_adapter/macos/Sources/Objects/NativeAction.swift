// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import Foundation

/// Native names are opaque; descriptions are localized aliases, not commands
/// to translate, normalize, parse or approximate.
enum NativeAction {
    struct Item: Equatable {
        let name: String
        let description: String?
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
