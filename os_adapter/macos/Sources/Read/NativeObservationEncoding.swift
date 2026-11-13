// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import Foundation
import ApplicationServices

/// The single publication boundary for a request-local AX collection.
/// Native field and relation records become properties of one shared graph.
enum NativeObservationEncoding {
    typealias Record = [String: Any]

    final class RelationReadTemplate {
        let record: Record
        init(_ record: Record) { self.record = record }
    }

    final class FieldIssueTemplate {
        let issues: [Record]
        let read: Record

        init(_ issue: Record) {
            issues = [issue]
            read = ["complete": false, "issues": issues]
        }
    }

    enum IssueEvidence {
        case raw([Record])
        case template(FieldIssueTemplate)

        var records: [Record] {
            switch self {
            case .raw(let records): return records
            case .template(let template): return template.issues
            }
        }
    }

    enum Read {
        case record(Record)
        case relationTemplate(RelationReadTemplate)
        case issueTemplate(FieldIssueTemplate)
        case batch(BatchRead)
        case acquiredBatch(Int, IssueEvidence?)
        case acquiredRelation(Record, source: Record)

        func record() -> Record {
            switch self {
            case .record(let value): return value
            case .relationTemplate(let value): return value.record
            case .issueTemplate(let value): return value.read
            case .batch(let value): return value.record()
            case .acquiredBatch: preconditionFailure("Unpooled batch read")
            case .acquiredRelation: preconditionFailure("Unpooled relation read")
            }
        }
    }

    struct BatchRead {
        let owner: Int
        let position: Int
        let issues: IssueEvidence?

        func details() -> [[Any]] {
            [["batch_owner", ["metadataValue": owner]], ["batch_index", ["metadataValue": position]]]
        }

        func record() -> Record {
            var read: Record = ["details": details()]
            if let issues {
                read["complete"] = false
                read["issues"] = issues.records
            }
            return read
        }
    }

    /// Common native leaves cross publication without an intermediate dictionary.
    /// Rich values and foreign records still use the generic JSON representation.
    enum CapturedValue {
        case record(Record)
        case null
        case boolean(Bool)
        case string(String)
        case point(Double, Double)
        case size(Double, Double)

        func materialize() -> Record {
            switch self {
            case .record(let value): return value
            case .null: return NativeObservationEncoding.value("CFNull", NSNull())
            case .boolean(let value): return NativeObservationEncoding.value("CFBoolean", value)
            case .string(let value): return NativeObservationEncoding.value("CFString", value)
            case .point(let x, let y):
                return Self.geometry("AXValue.CGPoint", first: ("x", x), second: ("y", y), metadata: Self.screenRecords)
            case .size(let width, let height):
                return Self.geometry(
                    "AXValue.CGSize", first: ("width", width), second: ("height", height), metadata: Self.sizeRecords)
            }
        }

        private static func geometry(
            _ type: String, first: (String, Double), second: (String, Double), metadata: [[Any]]
        ) -> Record {
            let fields = [first, second].map { name, coordinate in
                [name, NativeObservationEncoding.value("CGFloat", NativeObservationEncoding.floating(coordinate))] as [Any]
            }
            return NativeObservationEncoding.value(type, ["fields": fields], metadata: metadata)
        }

        static let sizeMetadata = [
            ("unit", "point"),
            ("coordinate_metadata_source", "api_contract")
        ]
        static let screenMetadata = [
            ("coordinate_space", "macos.ax.global_screen")
        ] + sizeMetadata

        // Both constants contain only value-semantic Swift strings and collections.
        nonisolated(unsafe) static let screenRecords = records(screenMetadata)
        nonisolated(unsafe) private static let sizeRecords = records(sizeMetadata)

        private static func records(_ metadata: [(String, String)]) -> [[Any]] {
            metadata.map { name, value in [name, NativeObservationEncoding.value("CFString", value)] as [Any] }
        }
    }

    struct Field {
        let namespace: String
        let name: String
        let value: CapturedValue?
        var state: String?
        var read: Read?

        init(namespace: String, name: String, value: Record?, state: String? = nil, read: Read? = nil) {
            self.init(namespace: namespace, name: name, capturedValue: value.map(CapturedValue.record),
                      state: state, read: read)
        }

        init(namespace: String, name: String, capturedValue: CapturedValue?, state: String? = nil, read: Read? = nil) {
            self.namespace = namespace
            self.name = name
            self.value = capturedValue
            self.state = state
            self.read = read
        }
    }

    static func catalogueValue(_ names: [String]) -> Record {
        value("CFArray", ["items": names.map { value("CFString", $0) }])
    }

    static func catalogueProperty(_ catalogue: Record) -> Record {
        ["key": ["toolkit", "attribute_names"], "value": catalogue]
    }

    enum Property {
        case field(Field)
        case raw(Record)
        case catalogue([String])

        func record() -> Record {
            switch self {
            case .raw(let value):
                return value
            case .catalogue(let names):
                return NativeObservationEncoding.catalogueProperty(NativeObservationEncoding.catalogueValue(names))
            case .field(let field):
                var value: Record = [
                    "key": [field.namespace, field.name], "value": field.value?.materialize() ?? NSNull()
                ]
                if let read = field.read { value["read"] = read.record() }
                if let state = field.state { value["state"] = state }
                return value
            }
        }

        mutating func poolRead(
            _ metadata: inout NativeReadMetadataPool, owner: String, ownerReference: inout Int?
        ) {
            switch self {
            case .catalogue(let names):
                let value = NativeObservationEncoding.catalogueValue(names)
                self = .raw(NativeObservationEncoding.catalogueProperty(value))
            case .raw(var property):
                if let read = property["read"] as? Record {
                    property["read"] = metadata.encodeRead(read)
                    self = .raw(property)
                }
            case .field(var field):
                switch field.read {
                case .record(let read):
                    field.read = .record(metadata.encodeRead(read))
                    self = .field(field)
                case .acquiredBatch(let index, let issues):
                    let ownerIndex = ownerReference ?? metadata.batchOwnerReference(owner)
                    ownerReference = ownerIndex
                    field.read = metadata.publishBatch(owner: ownerIndex, index: index, issues: issues)
                    self = .field(field)
                case .acquiredRelation(let read, let source):
                    field.read = metadata.encodeRelationRead(read, source: source)
                    self = .field(field)
                case .batch, .relationTemplate, .issueTemplate, nil: break
                }
            }
        }
    }

    struct PublishedNode {
        let id: String
        var properties: [Property]?
        var read: Record?
        var resource: Record?
        struct Acquisition {
            // Acquisition can spell the same canonical ID differently from
            // declaration. The batch owner keeps the acquired spelling.
            let owner: String
            let requestedFields: [String]
            let read: NativeCollectedObservation.NodeRead
        }
        var acquisition: Acquisition?

        func record() -> Record {
            if let resource { return resource }
            var result: Record = [
                "id": id, "source": "macos.ax", "kind": "element", "nativeType": "AXUIElement",
                "properties": properties.map { $0.map { $0.record() } as Any } ?? NSNull()
            ]
            if let read { result["read"] = read }
            return result
        }

        mutating func poolReads(
            _ metadata: inout NativeReadMetadataPool, catalogue: NativeReadMetadataPool.AttributeCatalogue?
        ) {
            if let resource {
                self.resource = metadata.encodeNode(resource, attributeCatalogue: catalogue)
                return
            }
            if let acquisition {
                switch acquisition.read {
                case .record(let original):
                    read = metadata.encodeRead(original, appendingRequestedFields: acquisition.requestedFields)
                case .batch(let fields, let status, let queried):
                    read = metadata.encodeBatchNodeRead(fields: fields, status: status, queried: queried,
                                                       requested: acquisition.requestedFields)
                }
            }
            let owner = acquisition?.owner ?? id
            acquisition = nil
            guard properties != nil else { return }
            if let shared = metadata.sharedCatalogue(catalogue) {
                switch properties![shared.position] {
                case .catalogue:
                    properties![shared.position] = .raw(NativeObservationEncoding.catalogueProperty(shared.value))
                case .raw(var property):
                    property["value"] = shared.value
                    properties![shared.position] = .raw(property)
                case .field:
                    preconditionFailure("An attribute catalogue must be a native property")
                }
            }
            var ownerReference: Int?
            for index in properties!.indices {
                properties![index].poolRead(&metadata, owner: owner, ownerReference: &ownerReference)
            }
        }
    }

    struct Publication {
        let session: Any
        let roots: [String]
        let nodes: [PublishedNode]
        let read: Record
        let metadataValues: [Record]

        func record() -> Record {
            [
                "kind": "observation", "session": session, "platform": "macos", "roots": roots,
                "nodes": nodes.map { $0.record() }, "read": read, "metadataValues": metadataValues
            ]
        }
    }

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

    private static func propertyState(hasValue: Bool, status: Record) -> String {
        let original = status["state"] as? String ?? "error"
        return !hasValue
            ? (["absent", "unsupported", "not_read", "error", "timeout", "unavailable", "redacted"].contains(original)
                ? original : "error") : "value"
    }

    private struct LexicalAction: Hashable {
        let name: String
        let description: String?

        static func == (lhs: Self, rhs: Self) -> Bool {
            guard lhs.name.utf8.elementsEqual(rhs.name.utf8) else { return false }
            if let left = lhs.description, let right = rhs.description {
                return left.utf8.elementsEqual(right.utf8)
            }
            return lhs.description == nil && rhs.description == nil
        }
    }

    private struct ActionValues {
        var values = [[LexicalAction]: Record]()

        mutating func value(_ items: Any) -> Record {
            // Original scalar construction recognizes these complete string-only
            // records. Unknown/bridged values use its original dispatch.
            let type = Swift.type(of: items)
            guard type == [Record].self || type == [Any].self,
                let rawRows = items as? [Any], rawRows.count <= 8
            else { return scalar(items) }
            var key = [LexicalAction]()
            for rawRow in rawRows {
                guard Swift.type(of: rawRow) == Record.self, let row = rawRow as? Record else { return scalar(items) }
                guard let rawName = row["name"], Swift.type(of: rawName) == String.self,
                    let name = rawName as? String, name.utf8.count <= 128
                else { return scalar(items) }
                if row.count == 1 {
                    key.append(.init(name: name, description: nil))
                } else if row.count == 2, let rawDescription = row["description"],
                    Swift.type(of: rawDescription) == String.self,
                    let description = rawDescription as? String, description.utf8.count <= 128
                {
                    key.append(.init(name: name, description: description))
                } else {
                    return scalar(items)
                }
            }
            if let hit = values[key] { return hit }
            let result = scalar(items)
            if values.count < 32 { values[key] = result }
            return result
        }
    }

    static func publish(_ batch: NativeCollectedObservation, shareAttributeCatalogues: Bool = false) -> Publication {
        let emptyRelationArray = value(
            "CFArray", ["items": [Record]()], metadata: [["native_cardinality", value("CFString", "array")]])
        let emptyRelationPage = value("relation_page", ["items": [Record]()])
        let records = batch.nodes
        var metadata = NativeReadMetadataPool()
        var catalogues = [Int: NativeReadMetadataPool.AttributeCatalogue]()
        var actionValues = ActionValues()
        let relations = batch.relations
        var indices = [String: Int](), nodes = [PublishedNode]()
        indices.reserveCapacity(records.count)
        nodes.reserveCapacity(records.count)
        @discardableResult func declare(_ id: String) -> Int {
            if let index = indices[id] { return index }
            let index = nodes.count
            indices[id] = index
            nodes.append(PublishedNode(id: id))
            return index
        }
        func declareReferences(_ raw: Any) {
            let type = Swift.type(of: raw)
            if type == String.self || type == Bool.self || type == Int.self || type == Double.self || type == NSNull.self
            {
                return
            }
            if let map = raw as? Record {
                if map.count == 2, map["nativeType"] is String, let data = map["data"] {
                    // The omitted type is a string and cannot declare an object.
                    declareReferences(data)
                    return
                }
                if let shared = metadata.resolved(map) {
                    declareReferences(shared)
                } else if map.count == 1, let id = map["ref"] as? String {
                    declare(id)
                } else {
                    for item in map.values { declareReferences(item) }
                }
            } else if let list = raw as? [Any] {
                for item in list { declareReferences(item) }
            }
        }
        let roots = batch.roots
        for root in roots { declare(root) }
        for record in records {
            let index = declare(record.id)
            var properties = [Property]()
            properties.reserveCapacity(record.requested.count + 1 + record.extra.count)
            for key in record.requested {
                let typed = record.nativeValues[key] ?? record.values[key].map { .record(scalar($0)) }
                if case .record(let value) = typed { declareReferences(value) }
                let status = record.statuses[key]
                var entry = Field(namespace: "macos.ax", name: key, capturedValue: typed)
                let issues = status.map { original in
                    entry.state = propertyState(hasValue: typed != nil, status: original)
                    return metadata.encodeFieldIssue(original) { issue(original) }
                }
                switch record.nativeReads[key] {
                case .batch(let position):
                    entry.read = .acquiredBatch(position, issues)
                case .record(var read):
                    if let issues {
                        read["complete"] = false
                        read["issues"] = issues.records
                    }
                    entry.read = .record(read)
                case nil:
                    switch issues {
                    case .template(let template): entry.read = .issueTemplate(template)
                    case .raw(let records): entry.read = .record(["complete": false, "issues": records])
                    case nil: break
                    }
                }
                properties.append(.field(entry))
            }
            let actions = record.actions
            let catalogue = actionValues.value(actions["items"] ?? [Record]())
            var actionProperty = Field(namespace: "toolkit", name: "actions", value: catalogue)
            var actionDetails = actions
            actionDetails.removeValue(forKey: "items")
            actionProperty.read = .record([
                "complete": actions["state"] as? String == "complete", "details": details(actionDetails)
            ])
            properties.append(.field(actionProperty))
            for entry in record.extra {
                switch entry {
                case .catalogue(let names):
                    if shareAttributeCatalogues {
                        catalogues[index] = metadata.trackAttributeNames(names, at: properties.count)
                    }
                    properties.append(.catalogue(names))
                case .raw(let record):
                    if shareAttributeCatalogues,
                        let catalogue = metadata.trackAttributeCatalogue(record, at: properties.count)
                    {
                        catalogues[index] = catalogue
                    } else {
                        declareReferences(record)
                    }
                    properties.append(.raw(record))
                }
            }
            nodes[index].properties = properties
            nodes[index].acquisition = .init(owner: record.id, requestedFields: record.requested, read: record.read)
        }
        for relation in relations {
            // The acquisition encoder constructs this internal field with the asserted type.
            // swiftlint:disable:next force_cast
            let source = relation["source"] as! String
            let sourceIndex = declare(source)
            // The acquisition encoder constructs this internal field with the asserted type.
            // swiftlint:disable:next force_cast
            let targets = relation["targets"] as! [String]
            for target in targets { declare(target) }
            let nativeType = relation["nativeType"] as? String
            let nativeAPI = relation["nativeAPI"] as? String
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
            } else if hasPayload && targets.isEmpty && nativeType?.utf8.elementsEqual("CFArray".utf8) == true {
                payload = emptyRelationArray
            } else if hasPayload && targets.isEmpty && nativeType == nil {
                payload = emptyRelationPage
            } else if hasPayload {
                payload = value(
                    nativeType ?? "relation_page", ["items": targets.map { value("AXUIElement", ["ref": $0]) }],
                    metadata: nativeType == "CFArray" ? [["native_cardinality", value("CFString", "array")]] : [])
            } else {
                payload = nil
            }
            // Relations own their final read evidence below. Constructing a
            // field failure first would intern status/continuation values that
            // this relation read replaces, leaving unreferenced pool entries.
            // The acquisition encoder constructs this internal field with the asserted type.
            // swiftlint:disable:next force_cast
            var entry = Field(namespace: "macos.ax", name: relation["property"] as! String, value: payload)
            if !hasPayload { entry.state = propertyState(hasValue: false, status: relation) }
            if hasPayload, payload == nil { entry.state = "not_read" }
            var read: Record = ["complete": ["complete", "absent"].contains(state)]
            if let nativeAPI { read["api"] = nativeAPI }
            if !["complete", "absent", "unsupported"].contains(state) || relation["wrappedNativeValue"] != nil {
                read["issues"] = [metadata.encodeIssue(relation) { issue(relation) }]
            }
            if let issues = read["issues"] { declareReferences(issues) }
            entry.read = .acquiredRelation(read, source: relation)
            if nodes[sourceIndex].properties == nil { nodes[sourceIndex].properties = [] }
            nodes[sourceIndex].properties!.append(.field(entry))
        }
        for id in batch.unread { declare(id) }
        for resource in batch.nativeResources {
            // The acquisition encoder constructs this internal field with the asserted type.
            // swiftlint:disable:next force_cast
            let id = resource["id"] as! String
            let index = declare(id)
            nodes[index].resource = resource
        }
        let observed = batch.stamp
        var meta: Record = [
            "surface": batch.surface, "observation_id": observed.id,
            "requested_relations": batch.requestedRelations ?? relations.compactMap { $0["property"] as? String }
        ]
        if let token = batch.changeToken { meta["change_token"] = token }
        let reasons = batch.stopReasons
        let read: Record = [
            "api": "ApplicationServices.AXUIElement", "producer": "computer-use-macos", "cached": false,
            "intervalNs": [observed.startedAt * 1_000_000, observed.completedAt * 1_000_000],
            "complete": reasons.isEmpty, "issues": reasons.map { ["reason": $0] }, "details": details(meta)
        ]
        // Pool indices are observable. Issues already interned during
        // declaration precede each node's read, catalogue and property reads.
        for index in nodes.indices { nodes[index].poolReads(&metadata, catalogue: catalogues[index]) }
        let publishedRead = metadata.encodeRead(read)
        return Publication(
            session: batch.session, roots: roots, nodes: nodes, read: publishedRead, metadataValues: metadata.values)
    }
}
