import Foundation
import ApplicationServices

/// The single publication boundary for a request-local AX collection.
/// Native field and relation records become properties of one shared graph.
enum NativeObservationEncoding {
    typealias Record = [String: Any]

    static func value(_ type: String, _ data: Any, metadata: [[Any]] = []) -> Record {
        var result: Record = ["nativeType": type, "data": data]
        if !metadata.isEmpty { result["metadata"] = metadata }
        return result
    }

    static func scalar(_ raw: Any) -> Record {
        if raw is NSNull { return value("CFNull", NSNull()) }
        if let number = raw as? NSNumber {
            if CFGetTypeID(number) == CFBooleanGetTypeID() { return value("CFBoolean", number.boolValue) }
            let type = String(cString: number.objCType)
            if ["f", "d"].contains(type) { return value("CFNumber." + type, floating(number.doubleValue)) }
            return value("CFNumber." + type, ["integer": number.stringValue])
        }
        if let text = raw as? String { return value("CFString", text) }
        if let items = raw as? [Any] { return value("CFArray", ["items": items.map(scalar)]) }
        if let fields = raw as? Record {
            return value("record", ["fields": fields.keys.sorted().map { [$0, scalar(fields[$0]!)] as [Any] }])
        }
        preconditionFailure("Unencoded native collection value: \(type(of: raw))")
    }

    static func floating(_ number: Double) -> Any {
        if number.isNaN { return ["float": "nan"] }
        if number == .infinity { return ["float": "inf"] }
        if number == -.infinity { return ["float": "-inf"] }
        if number == 0 && number.sign == .minus { return ["float": "-0"] }
        return number
    }

    static func details(_ values: Record) -> [[Any]] { values.keys.sorted().map { [$0, scalar(values[$0]!)] } }

    static func batchRead(fields: [String], status: Int32, queried: Bool) -> Record {
        [
            "api": "AXUIElementCopyMultipleAttributeValues",
            "details": details(["batch_fields": fields, "batch_status": status, "queried": queried])
        ]
    }

    static func batchPropertyRead(owner: String, index: Int) -> Record {
        ["details": [["batch_owner", value("AXUIElement", ["ref": owner])], ["batch_index", scalar(index)]]]
    }

    static func issue(_ status: Record) -> Record {
        var evidence = status
        let wrapped = evidence.removeValue(forKey: "wrappedNativeValue") as? Record
        var fields = details(evidence)
        if let wrapped { fields.append(["wrappedNativeValue", wrapped]) }
        let nativeStatus = value("record", ["fields": fields])
        var result: Record = ["reason": status["state"] as? String ?? "incomplete", "nativeStatus": nativeStatus]
        if let detail = status["detail"] as? String { result["detail"] = detail }
        var continuation: Record = [:]
        for key in ["textRef", "totalUnits", "cursor", "offset", "total"] {
            if let value = status[key] { continuation[key] = value }
        }
        if !continuation.isEmpty { result["continuation"] = scalar(continuation) }
        return result
    }

    private static func property(_ key: String, value: Record?, status: Record?, namespace: String = "macos.ax")
        -> Record
    {
        var property: Record = ["key": [namespace, key], "value": value ?? NSNull()]
        guard let status else { return property }
        let original = status["state"] as? String ?? "error"
        property["state"] =
            value == nil
            ? (["absent", "unsupported", "not_read", "error", "timeout", "unavailable", "redacted"].contains(original)
                ? original : "error") : "value"
        property["read"] = ["complete": false, "issues": [issue(status)]]
        return property
    }

    static func encode(_ batch: Record) -> Record {
        precondition(["nodes", "relation"].contains(batch["kind"] as? String ?? ""))
        let records = batch["nodes"] as? [Record] ?? []
        let relations = batch["relations"] as? [Record] ?? (batch["relation"] as? Record).map { [$0] } ?? []
        var nodes = [String: Record](), order = [String]()
        func declare(_ id: String) {
            if nodes[id] == nil {
                order.append(id)
                nodes[id] = [
                    "id": id, "source": "macos.ax", "kind": "element", "nativeType": "AXUIElement",
                    "properties": NSNull()
                ]
            }
        }
        func declareReferences(_ raw: Any) {
            if let map = raw as? Record {
                if map.count == 1, let id = map["ref"] as? String {
                    declare(id)
                } else {
                    for item in map.values { declareReferences(item) }
                }
            } else if let list = raw as? [Any] {
                for item in list { declareReferences(item) }
            }
        }
        let roots = batch["roots"] as? [String] ?? (relations.first?["source"] as? String).map { [$0] } ?? []
        for root in roots { declare(root) }
        for record in records {
            let id = record["node"] as! String
            declare(id)
            let values = record["nativeValues"] as? [String: Record] ?? [:]
            let rawValues = record["values"] as! Record
            let statuses = record["fieldStatus"] as! [String: Record]
            let reads = record["nativeReads"] as? [String: Record] ?? [:]
            var properties = [Record]()
            for key in record["requestedFields"] as? [String] ?? [] {
                let typed = values[key] ?? rawValues[key].map(scalar)
                if let typed { declareReferences(typed) }
                var entry = property(key, value: typed, status: statuses[key])
                if var read = reads[key] {
                    if let failure = entry["read"] as? Record { read.merge(failure) { _, failure in failure } }
                    entry["read"] = read
                }
                properties.append(entry)
            }
            let actions = record["actions"] as! Record
            let catalogue = scalar(actions["items"] ?? [Record]())
            var actionProperty = property("actions", value: catalogue, status: nil, namespace: "toolkit")
            var actionDetails = actions
            actionDetails.removeValue(forKey: "items")
            actionProperty["read"] = [
                "complete": actions["state"] as? String == "complete", "details": details(actionDetails)
            ]
            properties.append(actionProperty)
            for entry in record["nativeProperties"] as? [Record] ?? [] {
                declareReferences(entry)
                properties.append(entry)
            }
            nodes[id]!["properties"] = properties
            var nodeRead = record["nativeRead"] as? Record ?? ["api": "AXUIElementCopyMultipleAttributeValues"]
            var readDetails = nodeRead["details"] as? [[Any]] ?? []
            readDetails.append(["requested_fields", scalar(record["requestedFields"] ?? [])])
            nodeRead["details"] = readDetails
            nodes[id]!["read"] = nodeRead
        }
        for relation in relations {
            let source = relation["source"] as! String
            declare(source)
            let targets = relation["targets"] as! [String]
            for target in targets { declare(target) }
            var meta = relation
            meta.removeValue(forKey: "source")
            meta.removeValue(forKey: "property")
            meta.removeValue(forKey: "targets")
            let nativeType = meta.removeValue(forKey: "nativeType") as? String
            let nativeAPI = meta.removeValue(forKey: "nativeAPI") as? String
            meta.removeValue(forKey: "wrappedNativeValue")
            meta["relation"] = true
            let state = relation["state"] as? String ?? "error"
            let hasPayload = ["complete", "partial"].contains(state)
            let payload: Record?
            if hasPayload, nativeType == "AXUIElement" {
                precondition(targets.count <= 1, "a native single relationship cannot contain multiple targets")
                // A zero registration budget can leave a known single target
                // unexpanded. Its count is coverage, never a fake empty value.
                payload = targets.first.map {
                    value("AXUIElement", ["ref": $0], metadata: [["native_cardinality", value("CFString", "single")]])
                }
            } else if hasPayload {
                payload = value(
                    nativeType ?? "relation_page", ["items": targets.map { value("AXUIElement", ["ref": $0]) }],
                    metadata: nativeType == "CFArray" ? [["native_cardinality", value("CFString", "array")]] : [])
            } else {
                payload = nil
            }
            var entry = property(relation["property"] as! String, value: payload, status: hasPayload ? nil : relation)
            if hasPayload, payload == nil { entry["state"] = "not_read" }
            var read: Record = ["complete": ["complete", "absent"].contains(state), "details": details(meta)]
            if let nativeAPI { read["api"] = nativeAPI }
            if !["complete", "absent", "unsupported"].contains(state) || relation["wrappedNativeValue"] != nil {
                read["issues"] = [issue(relation)]
            }
            if let issues = read["issues"] { declareReferences(issues) }
            entry["read"] = read
            var properties = nodes[source]!["properties"] as? [Record] ?? []
            properties.append(entry)
            nodes[source]!["properties"] = properties
        }
        for id in batch["unread"] as? [String] ?? [] { declare(id) }
        for resource in batch["nativeResources"] as? [Record] ?? [] {
            let id = resource["id"] as! String
            declare(id)
            nodes[id] = resource
        }
        let observed = batch["observed"] as! Record
        var meta: Record = [
            "surface": batch["surface"]!, "observation_id": observed["id"]!,
            "requested_relations": batch["requestedRelations"] ?? relations.compactMap { $0["property"] as? String }
        ]
        if let token = batch["changeToken"] { meta["change_token"] = token }
        let reasons = batch["stopReasons"] as? [String] ?? []
        let read: Record = [
            "api": "ApplicationServices.AXUIElement", "producer": "computer-use-macos", "cached": false,
            "intervalNs": [
                ((observed["startedAt"] as! NSNumber).int64Value * 1_000_000),
                ((observed["completedAt"] as! NSNumber).int64Value * 1_000_000)
            ], "complete": reasons.isEmpty, "issues": reasons.map { ["reason": $0] }, "details": details(meta)
        ]
        return [
            "kind": "observation", "session": batch["session"]!, "platform": "macos", "roots": roots,
            "nodes": order.map { nodes[$0]! }, "read": read
        ]
    }
}
