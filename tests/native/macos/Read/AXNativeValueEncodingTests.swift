import Foundation
import ApplicationServices

@main @MainActor struct AXNativeValueEncodingTests {
    static func main() throws {
        let encoder = AXNativeValueEncoding()
        func encode(_ raw: CFTypeRef) throws -> [String: Any] { try encoder.encode(raw) { _ in "node" } }
        let zero = try encode(NSNumber(value: 0))
        precondition((zero["data"] as? [String: String])?["integer"] == "0")
        let boolean = try encode(kCFBooleanFalse)
        precondition(boolean["data"] as? Bool == false)
        for (number, tag) in [(Double.infinity, "inf"), (-Double.infinity, "-inf"), (.nan, "nan"), (-0.0, "-0")] {
            let value = try encode(NSNumber(value: number))
            precondition((value["data"] as? [String: String])?["float"] == tag)
            precondition(JSONSerialization.isValidJSONObject(value))
        }
        var point = CGPoint(x: -1920.25, y: -0.0)
        let geometry = try encode(AXValueCreate(.cgPoint, &point)!)
        precondition(geometry["nativeType"] as? String == "AXValue.CGPoint" && geometry["metadata"] != nil)
        var size = CGSize(width: -1, height: 0)
        let sizeValue = try encode(AXValueCreate(.cgSize, &size)!)
        precondition(sizeValue["nativeType"] as? String == "AXValue.CGSize")
        var range = CFRange(location: -1, length: 0)
        let rangeValue = try encode(AXValueCreate(.cfRange, &range)!)
        precondition(rangeValue["nativeType"] as? String == "AXValue.CFRange")
        var error = AXError.attributeUnsupported
        let errorValue = try encode(AXValueCreate(.axError, &error)!)
        precondition(errorValue["nativeType"] as? String == "AXValue.AXError")
        let nested: [String: Any] = [
            "null": NSNull(), "bytes": Data([0, 255]), "object": AXUIElementCreateApplication(getpid()),
            "list": [false, "", 0]
        ]
        let map = try encode(nested as CFDictionary)
        precondition(map["nativeType"] as? String == "CFDictionary" && JSONSerialization.isValidJSONObject(map))
        let attributed = NSAttributedString(string: "abcdef", attributes: [.init("native-key"): "value"])
        let rich = try encode(attributed)
        precondition(rich["nativeType"] as? String == "CFAttributedString" && JSONSerialization.isValidJSONObject(rich))
        let marker = Data([1, 2, 3, 4]).withUnsafeBytes {
            AXTextMarkerCreate(nil, $0.bindMemory(to: UInt8.self).baseAddress!, $0.count)
        }
        let markerValue = try encode(marker)
        precondition((markerValue["data"] as? [String: String])?["ref"] != nil)
        precondition(encoder.resources.count == 1 && encoder.resources[0]["kind"] as? String == "text_marker")
        precondition(JSONSerialization.isValidJSONObject(encoder.resources))
        let retained = AXNativeValueResources()
        let first = AXNativeValueEncoding(resources: retained), next = AXNativeValueEncoding(resources: retained)
        let firstValue = try first.encode(marker) { _ in "node" }
        let nextValue = try next.encode(marker) { _ in "node" }
        precondition(firstValue["data"] as? [String: String] == nextValue["data"] as? [String: String])
        precondition(next.resources.count == 1, "a reused resource is declared in every observation that references it")
        let invalidUnits: [UniChar] = [0xD800, 0x0041]
        let malformed = CFStringCreateWithCharacters(nil, invalidUnits, invalidUnits.count)!
        let preserved = try encode(malformed)
        precondition(preserved["raw"] as? String == Data([0x00, 0xD8, 0x41, 0x00]).base64EncodedString())
        let unbound = AXNativeValueEncoding()
        enum Budget: Error { case exhausted }
        let originalObject = AXUIElementCreateApplication(getpid())
        let deferred = try unbound.encode(["object", originalObject] as CFArray) { _ in throw Budget.exhausted }
        precondition(deferred["nativeType"] as? String == "CFArray")
        precondition(unbound.resources.count == 1 && unbound.resources[0]["kind"] as? String == "unknown")
        precondition((unbound.resources[0]["read"] as! [String: Any])["complete"] as? Bool == false)
        let dependencyGraph = try verifyResourceDependencyEviction()
        if CommandLine.arguments.count == 2 {
            try JSONSerialization.data(withJSONObject: dependencyGraph).write(
                to: URL(fileURLWithPath: CommandLine.arguments[1]))
        }
        print(
            """
            PASS: CF types, signed geometry/ranges, special numbers, nested references, rich text and marker \
            bytes are lossless
            """)
    }

    static func verifyResourceDependencyEviction() throws -> [String: Any] {
        func marker(_ number: UInt32) -> AXTextMarker {
            var number = number
            return withUnsafeBytes(of: &number) {
                AXTextMarkerCreate(nil, $0.bindMemory(to: UInt8.self).baseAddress!, $0.count)
            }
        }
        let retained = AXNativeValueResources()
        let start = marker(1), end = marker(2)
        let first = AXNativeValueEncoding(resources: retained)
        _ = try first.encode(start) { _ in "node" }
        _ = try first.encode(end) { _ in "node" }
        for number in 3...1002 { _ = try first.encode(marker(UInt32(number))) { _ in "node" } }
        let range = AXTextMarkerRangeCreate(nil, start, end)
        let original = try first.encode(range) { _ in "node" }
        let id = (original["data"] as! [String: String])["ref"]!
        let published = first.resources.first { $0["id"] as? String == id }!
        let before = try JSONSerialization.data(withJSONObject: published, options: [.sortedKeys])
        for number in 1003...2048 { _ = try first.encode(marker(UInt32(number))) { _ in "node" } }

        let second = AXNativeValueEncoding(resources: retained)
        let value = try second.encode(range) { _ in "node" }
        precondition(
            (value["data"] as! [String: String])["ref"] == id,
            "refreshing dependencies must retain the same live range binding")
        let after = try JSONSerialization.data(
            withJSONObject: first.resources.first { $0["id"] as? String == id }!, options: [.sortedKeys])
        precondition(before == after, "refreshing the resource cache must not mutate an earlier capture")
        let graph = NativeObservationEncoding.encode([
            "kind": "nodes", "session": "resource-eviction", "platform": "macos", "surface": "scope",
            "observed": ["id": "eviction", "startedAt": 1000, "completedAt": 1001], "roots": ["node"],
            "nodes": [
                [
                    "node": "node", "requestedFields": ["AXValue"], "values": [String: Any](),
                    "nativeValues": ["AXValue": value], "fieldStatus": [String: Any](),
                    "actions": ["state": "complete", "total": 0, "items": [[String: Any]]()]
                ]
            ], "relations": [[String: Any]](), "requestedRelations": [String](), "unread": [String](),
            "stopReasons": [String](), "nativeResources": second.resources
        ])
        let ids = Set((graph["nodes"] as! [[String: Any]]).map { $0["id"] as! String })
        func checkReferences(_ raw: Any) {
            if let record = raw as? [String: Any] {
                if record.count == 1, let ref = record["ref"] as? String {
                    precondition(ids.contains(ref), "resource dependencies must be declared within this graph")
                } else {
                    for value in record.values { checkReferences(value) }
                }
            } else if let items = raw as? [Any] {
                for item in items { checkReferences(item) }
            }
        }
        checkReferences(graph)
        precondition(second.resources.count == 3)
        print(
            """
            PASS: evicting marker dependencies refreshes a retained range identity and leaves published \
            graphs unchanged
            """)
        return graph
    }
}
