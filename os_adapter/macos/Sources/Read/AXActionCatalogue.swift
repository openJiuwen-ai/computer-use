// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import ApplicationServices
import Foundation

/// Explicit query demand; an omitted value keeps complete observation behavior.
enum NativeActionDetail: String { case none, names, full }

/// Read only action evidence consumed by this collection's declared purpose.
@MainActor enum AXActionCatalogue {
    struct Result {
        let facts: [String: Any]
        let nativeProperties: [[String: Any]]
    }

    static func collect(
        detail: NativeActionDetail, withinBudget: () throws -> Bool,
        names: () -> (AXError, CFArray?), description: (String) -> (AXError, CFString?),
        encode: (CFTypeRef) throws -> [String: Any]
    ) throws -> Result {
        if detail == .none {
            return Result(
                facts: ["state": "not_read", "items": [[String: Any]](),
                        "detail": "action catalogue not requested by this query profile"], nativeProperties: [])
        }
        let available = try withinBudget()
        let (status, names) = available ? names() : (.cannotComplete, nil)
        var facts = AXFactEncoding.actions(status, names)
        if !available { facts["detail"] = "collection time budget" }
        var nativeProperties = [[String: Any]]()
        if status == .success, let names {
            nativeProperties.append(["key": ["toolkit", "action_names"], "value": try encode(names)])
        }
        // AXFactEncoding constructs these entries from validated native names.
        // swiftlint:disable:next force_cast
        var actions = facts["items"] as! [[String: Any]]
        for index in actions.indices {
            // swiftlint:disable:next force_cast
            let name = actions[index]["name"] as! String
            if detail == .names {
                actions[index] = [
                    "name": name, "descriptionStatus": ["state": "not_read",
                                                        "detail": "description not requested by this query profile"]
                ]
            } else if try withinBudget() {
                let (status, value) = description(name)
                actions[index] = ["name": name].merging(AXFactEncoding.description(status, value)) { _, next in next }
                if status == .success, let value {
                    nativeProperties.append([
                        "key": ["toolkit", "action_description"],
                        "arguments": [["nativeType": "CFString", "data": name]], "value": try encode(value)
                    ])
                }
            }
        }
        facts["items"] = actions
        return Result(facts: facts, nativeProperties: nativeProperties)
    }
}
