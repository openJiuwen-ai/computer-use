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
        finalRelationEvidenceOwnsItsPoolValues()
        sharedFieldFailuresRemainShared()
        print("PASS lossless request-local metadata sharing and independent native object references")
    }

    static func finalRelationEvidenceOwnsItsPoolValues() {
        for state in ["absent", "unsupported", "error", "timeout", "unavailable", "redacted", "not_read", "complete", "partial"] {
            for wrapped in [false, true] {
                var relation: Record = [
                    "source": "original", "property": "AXChildren", "targets": [String](),
                    "state": state, "certainty": "confirmed", "total": 0, "detail": "original provider evidence",
                    "nativeType": "CFArray", "nativeAPI": "AXUIElementCopyAttributeValues"
                ]
                if wrapped {
                    relation["wrappedNativeValue"] = NativeObservationEncoding.value(
                        "AXUIElement", ["ref": "retained-error-object"])
                }
                let graph = NativeObservationEncoding.encode(batch(relations: [relation]))
                requireNoOrphanMetadata(graph)
                let nodes = graph["nodes"] as! [Record]
                let source = nodes.first { $0["id"] as? String == "original" }!
                let properties = source["properties"] as! [Record]
                let property = properties.first { $0["key"] as? [String] == ["macos.ax", "AXChildren"] }!
                let read = property["read"] as! Record
                let hasPayload = ["complete", "partial"].contains(state)
                precondition(property["state"] as? String == (hasPayload ? nil : state))
                precondition(read["complete"] as? Bool == ["complete", "absent"].contains(state))
                precondition(read["api"] as? String == "AXUIElementCopyAttributeValues")
                let issues = read["issues"] as? [Record]
                let needsIssue = !["complete", "absent", "unsupported"].contains(state) || wrapped
                precondition((issues != nil) == needsIssue,
                             "final relation evidence must retain real/wrapped errors without invented absence issues")
                if let issues {
                    precondition(issues.count == 1 && issues[0]["reason"] as? String == state)
                    precondition(issues[0]["nativeStatus"] != nil && issues[0]["continuation"] != nil,
                                 "actual status and continuation metadata must remain available")
                }
                if wrapped {
                    precondition(nodes.contains { $0["id"] as? String == "retained-error-object" },
                                 "a wrapped native reference still requires its independent graph declaration")
                }
            }
        }
    }

    static func sharedFieldFailuresRemainShared() {
        let failure: Record = ["state": "error", "detail": "shared provider failure", "total": 7]
        let row: Record = [
            "node": "original", "requestedFields": ["AXHelp", "AXDescription"], "values": Record(),
            "fieldStatus": ["AXHelp": failure, "AXDescription": failure],
            "actions": ["state": "not_read", "items": [Record]()]
        ]
        var raw = batch(relations: [
            ["source": "original", "property": "AXParent", "targets": [String](), "state": "unsupported"]
        ])
        raw["nodes"] = [row]
        let graph = NativeObservationEncoding.encode(raw)
        requireNoOrphanMetadata(graph)
        let source = (graph["nodes"] as! [Record]).first { $0["id"] as? String == "original" }!
        let properties = source["properties"] as! [Record]
        let fields = properties.filter { ($0["key"] as! [String])[0] == "macos.ax" && $0["state"] as? String == "error" }
        precondition(fields.count == 2)
        let statusRefs = fields.map { (($0["read"] as! Record)["issues"] as! [Record])[0]["nativeStatus"] as! Record }
        precondition(statusRefs[0]["metadataValue"] as? Int == statusRefs[1]["metadataValue"] as? Int,
                     "relations cannot disable the existing shared metadata evidence of independent fields")
    }

    static func batch(relations: [Record]) -> Record {
        ["kind": "nodes", "session": "s", "surface": "window", "roots": ["original"],
         "observed": ["id": "capture", "startedAt": 1, "completedAt": 2], "nodes": [Record](),
         "relations": relations, "unread": [String](), "stopReasons": [String]()]
    }

    static func requireNoOrphanMetadata(_ graph: Record) {
        var used = Set<Int>()
        func visit(_ raw: Any) {
            if let fields = raw as? Record {
                if let reference = fields["metadataValue"] as? Int {
                    precondition(fields.count == 1)
                    used.insert(reference)
                } else {
                    for value in fields.values { visit(value) }
                }
            } else if let items = raw as? [Any] {
                for value in items { visit(value) }
            }
        }
        var published = graph
        let values = published.removeValue(forKey: "metadataValues") as! [Record]
        visit(published)
        precondition(used == Set(values.indices), "publication emitted unreferenced metadata, or lost effective evidence")
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
