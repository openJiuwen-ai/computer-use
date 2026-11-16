// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import Foundation

@main enum NativeObservationLineParity {
    static func main() throws {
        typealias Encoding = NativeObservationEncoding
        let root = "root"
        let requested = ["AXRole", "AXTitle", "AXPosition", "AXSize", "AXDescription", "AXEnabled", "AXValue"]
        let node = NativeCollectedObservation.Node(
            id: root, requested: requested,
            values: ["AXRole": "AXWindow", "AXTitle": "中文 / \"quoted\" \n🙂"],
            statuses: ["AXDescription": ["state": "unsupported"]],
            actions: ["state": "complete", "items": [Encoding.Record]()],
            extra: [.catalogue(requested)],
            read: .batch(fields: requested, status: 0, queried: true),
            nativeReads: ["AXRole": .batch(0), "AXTitle": .batch(1), "AXDescription": .batch(4)],
            nativeValues: [
                "AXPosition": .point(-123.25, 4.5),
                "AXSize": .size(800, 600),
                "AXTitle": .string("中文 / \"quoted\" \n🙂"),
                "AXEnabled": .boolean(true),
                "AXValue": .null
            ])
        let batch = NativeCollectedObservation(
            session: "session", surface: "window",
            stamp: .init(id: "capture", startedAt: 1, completedAt: 2),
            roots: [root], nodes: [node],
            relations: [["source": root, "property": "AXChildren", "targets": ["child", "child"],
                         "state": "partial", "certainty": "confirmed", "total": 3, "cursor": "cursor"]],
            requestedRelations: ["AXChildren"], unread: ["child"], stopReasons: ["partial capture"])
        for share in [false, true] {
            let result = Encoding.publish(batch, shareAttributeCatalogues: share)
            let old: Encoding.Record = ["id": 7, "session": "session", "version": 6, "result": result.record()]
            var expected = try JSONSerialization.data(withJSONObject: old, options: [.sortedKeys, .fragmentsAllowed])
            expected.append(10)
            let actual = NativeObservationLine.encode(result, id: 7, session: "session", version: 6)
            precondition(actual == expected, "The ordinary reply must match Foundation's original JSONL bytes")
            precondition(NativeObservationLine.encode(
                result, id: 7, session: "session", version: 6, maximumBytes: 32) == nil)
        }

        // Known fragments can be composed even when a geometry coordinate
        // uses the original tagged spelling for negative zero or infinity.
        let special = NativeCollectedObservation.Node(
            id: root, requested: ["AXPosition"],
            values: [:], statuses: [:], actions: [:], read: .record([:]), nativeReads: [:],
            nativeValues: ["AXPosition": .point(-0.0, .infinity)])
        let graph = Encoding.publish(.init(session: "session", surface: "window",
            stamp: batch.stamp, roots: [root], nodes: [special], relations: []))
        let envelope: Encoding.Record = ["id": 7, "session": "session", "version": 6, "result": graph.record()]
        var reference = try JSONSerialization.data(withJSONObject: envelope, options: [.sortedKeys, .fragmentsAllowed])
        reference.append(10)
        precondition(NativeObservationLine.encode(graph, id: 7, session: "session", version: 6) == reference)

        try verifyRelationDetailsOutsideSourceTemplates()
    }

    private static func verifyRelationDetailsOutsideSourceTemplates() throws {
        typealias Encoding = NativeObservationEncoding
        typealias Record = Encoding.Record
        let root = "root"
        let readDetails: [Record] = [
            ["state": "timeout", "certainty": "unknown", "detail": "collection time budget"],
            ["state": "partial", "certainty": "confirmed", "detail": "child identity changed",
             "offset": 0, "total": 2, "cursor": "next"],
            ["state": "complete", "certainty": "confirmed", "total": 1]
        ]
        let sourceFields: [Record] = [
            ["property": "AXTitleUIElement", "targets": [String]()],
            ["property": "AXChildren", "targets": ["child"], "nativeType": "CFArray",
             "nativeAPI": "AXUIElementCopyAttributeValues"],
            ["property": "AXParent", "targets": ["parent"], "nativeType": "AXUIElement",
             "nativeAPI": "AXUIElementCopyAttributeValue"]
        ]
        let relations = zip(readDetails, sourceFields).map { details, fields -> Record in
            var relation = details.merging(fields) { old, _ in old }
            relation["source"] = root
            return relation
        }
        let observation = Encoding.publish(.init(
            session: "session", surface: "window", stamp: .init(id: "capture", startedAt: 1, completedAt: 2),
            roots: [root], relations: relations))
        let published = observation.record()
        let nodes = published["nodes"] as! [Record]
        let properties = nodes.first { $0["id"] as? String == root }!["properties"] as! [Record]
        let pool = published["metadataValues"] as! [Record]
        precondition(properties.count == relations.count)
        for (property, rawDetails) in zip(properties, readDetails) {
            let read = property["read"] as! Record
            let details = (read["details"] as! [[Any]]).map { pair -> [Any] in
                let index = (pair[1] as! Record)["metadataValue"] as! Int
                return [pair[0], pool[index]]
            }
            var expected = rawDetails
            expected["relation"] = true
            let actualBytes = try JSONSerialization.data(withJSONObject: details, options: [.sortedKeys])
            let expectedBytes = try JSONSerialization.data(
                withJSONObject: Encoding.details(expected), options: [.sortedKeys])
            precondition(actualBytes == expectedBytes, "Non-template relations must publish all read details")
        }
    }
}
