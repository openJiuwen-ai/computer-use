import Foundation
import ApplicationServices

@main struct NativeObservationEncodingTests {
    static func main() throws {
        let point = NativeObservationEncoding.value(
            "AXValue.CGPoint",
            [
                "fields": [
                    ["x", NativeObservationEncoding.value("CGFloat", -1920.25)],
                    ["y", NativeObservationEncoding.value("CGFloat", ["float": "-0"])]
                ]
            ], metadata: [["unit", NativeObservationEncoding.value("CFString", "point")]])
        let record: [String: Any] = [
            "kind": "nodes", "session": "test", "surface": "surface", "platform": "macos",
            "observed": ["id": "capture", "startedAt": 1_000, "completedAt": 1_500], "roots": ["a"],
            "nodes": [
                [
                    "node": "a", "requestedFields": ["AXPosition", "AXValue", "AXEnabled", "AXUnknown"],
                    "values": ["AXEnabled": false, "AXValue": "preview"], "nativeValues": ["AXPosition": point],
                    "fieldStatus": [
                        "AXValue": ["state": "truncated", "textRef": "text", "totalUnits": 40_000],
                        "AXUnknown": ["state": "unsupported", "detail": "AXError -25205"]
                    ],
                    "actions": [
                        "state": "partial", "total": 2,
                        "items": [
                            [
                                "name": "AXPress", "description": "",
                                "descriptionStatus": ["state": "timeout", "detail": "budget"]
                            ]
                        ]
                    ]
                ]
            ], "requestedRelations": ["AXChildren", "AXParent"],
            "relations": [
                [
                    "source": "a", "property": "AXChildren", "targets": ["b", "b"], "state": "partial",
                    "certainty": "confirmed", "offset": 0, "total": 3, "cursor": "cursor", "nativeType": "CFArray"
                ],
                [
                    "source": "a", "property": "AXParent", "targets": ["a"], "state": "complete",
                    "certainty": "confirmed", "total": 1, "nativeType": "AXUIElement",
                    "nativeAPI": "AXUIElementCopyAttributeValue"
                ]
            ], "unread": ["b"], "stopReasons": ["partial native relation"]
        ]
        let graph = NativeObservationEncoding.encode(record)
        precondition(graph["kind"] as? String == "observation" && graph["relations"] == nil && graph["unread"] == nil)
        precondition(graph["surface"] == nil && graph["observed"] == nil)
        let nodes = graph["nodes"] as! [[String: Any]]
        precondition(nodes.count == 2 && nodes[1]["id"] as? String == "b" && nodes[1]["properties"] is NSNull)
        let properties = nodes[0]["properties"] as! [[String: Any]]
        precondition(properties.count == 7)
        precondition((properties[0]["value"] as! [String: Any])["nativeType"] as? String == "AXValue.CGPoint")
        precondition(properties[1]["state"] as? String == "value")
        let partialRead = properties[1]["read"] as! [String: Any]
        precondition(partialRead["complete"] as? Bool == false)
        let issue = (partialRead["issues"] as! [[String: Any]])[0]
        precondition(issue["continuation"] != nil)
        precondition(properties[3]["state"] as? String == "unsupported" && properties[3]["value"] is NSNull)
        precondition(properties[4]["key"] as? [String] == ["toolkit", "actions"])
        let relation = properties[5]["value"] as! [String: Any]
        let items = (relation["data"] as! [String: Any])["items"] as! [[String: Any]]
        precondition(items.count == 2 && items[0]["data"] as? [String: String] == ["ref": "b"])
        let single = properties[6]["value"] as! [String: Any]
        precondition(
            single["nativeType"] as? String == "AXUIElement" && single["data"] as? [String: String] == ["ref": "a"])
        precondition((properties[6]["read"] as! [String: Any])["api"] as? String == "AXUIElementCopyAttributeValue")
        let read = graph["read"] as! [String: Any]
        precondition(read["intervalNs"] as? [Int64] == [1_000_000_000, 1_500_000_000])
        precondition(JSONSerialization.isValidJSONObject(graph))

        let relationOnly = NativeObservationEncoding.encode([
            "kind": "relation", "session": "test", "surface": "surface", "platform": "macos",
            "observed": ["id": "relation", "startedAt": 2_000, "completedAt": 2_001],
            "relation": [
                "source": "a", "property": "AXChildren", "targets": ["b"], "state": "complete",
                "certainty": "confirmed", "total": 1
            ]
        ])
        precondition(relationOnly["kind"] as? String == "observation" && relationOnly["relation"] == nil)
        precondition((relationOnly["nodes"] as! [[String: Any]]).count == 2)
        let unexpanded = NativeObservationEncoding.encode([
            "kind": "relation", "session": "test", "surface": "surface", "platform": "macos",
            "observed": ["id": "unexpanded", "startedAt": 2_000, "completedAt": 2_001],
            "relation": [
                "source": "a", "property": "AXParent", "targets": [String](), "state": "partial",
                "certainty": "confirmed", "total": 1, "nativeType": "AXUIElement",
                "nativeAPI": "AXUIElementCopyAttributeValue"
            ]
        ])
        let omitted = ((unexpanded["nodes"] as! [[String: Any]])[0]["properties"] as! [[String: Any]])[0]
        precondition(omitted["state"] as? String == "not_read" && omitted["value"] is NSNull)
        let malformed = NativeObservationEncoding.encode([
            "kind": "relation", "session": "test", "surface": "surface", "platform": "macos",
            "observed": ["id": "malformed", "startedAt": 2_000, "completedAt": 2_001],
            "relation": [
                "source": "a", "property": "AXParent", "targets": [String](), "state": "error", "certainty": "unknown",
                "wrappedNativeValue": NativeObservationEncoding.value(
                    "CFArray",
                    [
                        "items": [
                            NativeObservationEncoding.value("AXUIElement", ["ref": "b"]),
                            NativeObservationEncoding.value("CFNull", NSNull())
                        ]
                    ])
            ]
        ])
        let malformedNodes = malformed["nodes"] as! [[String: Any]]
        precondition(
            malformedNodes.count == 2,
            "object references in malformed native values still require shared graph declarations")
        let failed = (malformedNodes[0]["properties"] as! [[String: Any]])[0]
        precondition(failed["state"] as? String == "error" && failed["value"] is NSNull)
        precondition(((failed["read"] as! [String: Any])["issues"] as! [[String: Any]])[0]["nativeStatus"] != nil)
        precondition(JSONSerialization.isValidJSONObject(malformed))
        if CommandLine.arguments.count == 2 {
            try JSONSerialization.data(withJSONObject: graph, options: [.sortedKeys]).write(
                to: URL(fileURLWithPath: CommandLine.arguments[1]))
        }
        try verifyLinearBatchMetadata()
        print(
            """
            PASS: AX observations retain native values, errors, text/relation continuations, shared \
            references and graph cycles
            """)
    }

    static func verifyLinearBatchMetadata() throws {
        func size(nodes count: Int, fields fieldCount: Int) throws -> Int {
            let fields = (0..<fieldCount).map { "AXField\($0)" }
            let records: [[String: Any]] = (0..<count).map { index in
                let id = "node-\(index)"
                return [
                    "node": id, "requestedFields": fields,
                    "values": Dictionary(uniqueKeysWithValues: fields.map { ($0, "small value") }),
                    "fieldStatus": [String: Any](),
                    "actions": ["state": "complete", "total": 0, "items": [[String: Any]]()],
                    "nativeRead": NativeObservationEncoding.batchRead(fields: fields, status: 0, queried: true),
                    "nativeReads": Dictionary(
                        uniqueKeysWithValues: fields.enumerated().map { (offset, field) in
                            (field, NativeObservationEncoding.batchPropertyRead(owner: id, index: offset))
                        })
                ]
            }
            let graph = NativeObservationEncoding.encode([
                "kind": "nodes", "session": "size", "platform": "macos", "surface": "surface",
                "observed": ["id": "size", "startedAt": 1_000, "completedAt": 1_500], "roots": ["node-0"],
                "nodes": records, "relations": [[String: Any]](), "requestedRelations": [String](),
                "stopReasons": [String]()
            ])
            let payload = try JSONSerialization.data(withJSONObject: graph)
            let text = String(data: payload, encoding: .utf8)!
            precondition(
                text.components(separatedBy: "\"batch_fields\"").count - 1 == count,
                "the native batch profile belongs to its owner, not every field")
            return payload.count
        }
        let base = try size(nodes: 10, fields: 30)
        let moreNodes = try size(nodes: 20, fields: 30)
        let moreFields = try size(nodes: 10, fields: 60)
        let large = try size(nodes: 1000, fields: 60)
        precondition(moreNodes < base * 21 / 10 && moreFields < base * 23 / 10)
        precondition(large < 64 * 1024 * 1024)
        print(
            """
            PASS native graph size: 10x30=\(base), 20x30=\(moreNodes), 10x60=\(moreFields), 1000x60=\(large) \
            bytes; metadata scales linearly
            """)
    }
}
