// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import ApplicationServices

@main enum NativeApplicationContentTests {
    @MainActor static func main() throws {
        let query = try NativeInspectQuery(["kind": "roots", "application": "app", "applicationContent": true])
        guard case .roots("app", true) = query else {
            preconditionFailure("The explicit content profile must survive the native wire parser.")
        }
        let application = AXUIElementCreateApplication(100)
        let list = AXUIElementCreateApplication(101)
        let item = AXUIElementCreateApplication(102)
        let foreign = AXUIElementCreateApplication(103)
        var itemRole = "AXButton"
        var api = AXRelationReader.API()
        api.count = { _, _ in (.attributeUnsupported, 0) }
        api.single = { element, property in
            let values: [AXUIElement]
            if property == kAXWindowsAttribute { values = [] }
            else if CFEqual(element, application) { values = [list] }
            else if CFEqual(element, list) { values = [item] }
            else { values = [] }
            return (.success, values as CFArray)
        }
        func qualifies(_ target: AXUIElement?) throws -> NativeApplicationContent.Evidence {
            try NativeApplicationContent.evidence(
                application, pid: 100, containing: target,
                role: { element in
                    if CFEqual(element, application) { return kAXApplicationRole }
                    if CFEqual(element, list) { return kAXListRole }
                    return itemRole
                },
                elements: { element, property, limit in
                    try NativeApplicationContent.array(element, property, limit: limit, api: api, withinBudget: { true })
                },
                owner: { _ in 100 },
                withinBudget: { true })
        }
        let observed = try qualifies(item)
        precondition(observed == .confirmed, "The provider's complete arrays survive an unsupported count getter.")
        let outside = try qualifies(foreign)
        precondition(outside == .absent, "An unrelated object cannot borrow the application content scope.")
        itemRole = kAXWindowRole
        let document = try qualifies(nil)
        precondition(document == .absent, "A descendant window retains its own scope even when AXWindows is empty.")
        print("PASS windowless content retains exact membership and complete native arrays")
    }
}
