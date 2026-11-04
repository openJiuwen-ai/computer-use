// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import Foundation

@main enum NativeReadMetadataPoolTests {
    typealias Record = [String: Any]
    static func main() {
        var pool = NativeReadMetadataPool()
        let owner = NativeObservationEncoding.value("AXUIElement", ["ref": "original"])
        let read: Record = ["details": [["owner", owner], ["same_owner", owner]]]
        let published = pool.encodeRead(read)
        let details = published["details"] as! [[Any]]
        precondition(pool.values.count == 1)
        precondition((details[0][1] as! Record)["metadataValue"] as? Int == 0)
        precondition((details[1][1] as! Record)["metadataValue"] as? Int == 0)
        precondition((pool.values[0]["data"] as! Record)["ref"] as? String == "original")

        let integer = NativeObservationEncoding.value("CFNumber.i", ["integer": "1"])
        let boolean = NativeObservationEncoding.value("CFBoolean", true)
        let differentType = NativeObservationEncoding.value("CFNumber.q", ["integer": "1"])
        _ = pool.encodeRead(["details": [["integer", integer], ["boolean", boolean], ["wide", differentType]]])
        precondition(pool.values.count == 4, "sharing cannot collapse native numeric types or booleans")

        var constructs = 0
        let status: Record = ["state": "unsupported", "nativeCode": -25205]
        for _ in 0..<1000 {
            _ = pool.encodeIssue(status) {
                constructs += 1
                return NativeObservationEncoding.issue(status)
            }
        }
        precondition(constructs == 1, "repeated source evidence must not be expanded before discovering equality")

        let property: Record = ["key": ["macos.ax", "AXValue"], "value": owner, "read": read]
        let node = pool.encodeNode(["id": "original", "properties": [property], "read": read])
        let value = (node["properties"] as! [Record])[0]["value"] as! Record
        precondition(value["nativeType"] as? String == "AXUIElement" && value["metadataValue"] == nil,
                     "metadata indices cannot replace executable native-reference values")
        for item in pool.values {
            precondition(item["nativeType"] is String && item["data"] != nil && item["metadataValue"] == nil)
        }
        referenceInPooledFailureRemainsDeclared()
        print("PASS lossless request-local metadata sharing and independent native object references")
    }

    static func referenceInPooledFailureRemainsDeclared() {
        let raw: Record = [
            "kind": "nodes", "session": "s", "surface": "window", "roots": ["original"],
            "observed": ["id": "capture", "startedAt": 1, "completedAt": 2], "nodes": [Record](),
            "relations": [[
                "source": "original", "property": "AXParent", "targets": [String](), "state": "error",
                "wrappedNativeValue": NativeObservationEncoding.value("AXUIElement", ["ref": "retained-error-object"])
            ]], "unread": [String](), "stopReasons": [String]()
        ]
        let graph = NativeObservationEncoding.encode(raw)
        precondition((graph["nodes"] as! [Record]).contains { $0["id"] as? String == "retained-error-object" },
                     "pooling evidence must not hide native references from declaration or validation")
    }
}
