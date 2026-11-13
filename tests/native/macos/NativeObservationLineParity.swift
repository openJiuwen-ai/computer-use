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
    }
}
