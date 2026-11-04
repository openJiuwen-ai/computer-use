// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import ApplicationServices
import Foundation

@main enum NativeQueryActionProfileTests {
    @MainActor static func main() throws {
        for profile in [NativeActionDetail.none, .names, .full] {
            var namesCalls = 0, descriptionCalls = 0
            let result = try AXActionCatalogue.collect(
                detail: profile, withinBudget: { true },
                names: { namesCalls += 1; return (.success, ["AXPress", "AXShowMenu"] as CFArray) },
                description: { name in descriptionCalls += 1; return (.success, (name + " description") as CFString) },
                encode: { _ in ["nativeType": "provider", "data": "encoded result"] })
            let items = result.facts["items"] as! [[String: Any]]
            switch profile {
            case .none:
                precondition(namesCalls == 0 && descriptionCalls == 0)
                precondition(result.facts["state"] as? String == "not_read" && items.isEmpty)
                precondition(result.nativeProperties.isEmpty)
            case .names:
                precondition(namesCalls == 1 && descriptionCalls == 0)
                precondition(result.facts["state"] as? String == "complete")
                precondition(items.map { $0["name"] as! String } == ["AXPress", "AXShowMenu"])
                precondition(items.allSatisfy { ($0["descriptionStatus"] as? [String: String])?["state"] == "not_read" })
                precondition(items.allSatisfy { $0["description"] == nil })
                precondition(result.nativeProperties.count == 1)
            case .full:
                precondition(namesCalls == 1 && descriptionCalls == 2)
                precondition(items.allSatisfy { $0["description"] is String })
                precondition(result.nativeProperties.count == 3)
            }
        }
        let failed = try AXActionCatalogue.collect(
            detail: .names, withinBudget: { true }, names: { (.cannotComplete, nil) },
            description: { _ in preconditionFailure("descriptions were not requested") },
            encode: { _ in preconditionFailure("no native values were returned") })
        precondition(failed.facts["state"] as? String == "unavailable")
        let empty = try AXActionCatalogue.collect(
            detail: .names, withinBudget: { true }, names: { (.success, [] as CFArray) },
            description: { _ in preconditionFailure("there are no descriptions") },
            encode: { _ in ["nativeType": "CFArray", "data": ["items": []]] })
        precondition(empty.facts["state"] as? String == "complete" && empty.facts["total"] as? Int == 0)
        print("PASS query action profiles preserve not-read/names/full evidence and skip unrequested AX calls")
    }
}
