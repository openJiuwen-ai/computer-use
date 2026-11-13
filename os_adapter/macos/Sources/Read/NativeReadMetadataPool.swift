// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import CoreFoundation
import Foundation

/// Request-local sharing of immutable metadata values. These indices are wire
/// representation only: native object references remain AXUIElement/data.ref.
/// Entries contain complete values and never refer back into this pool.
struct NativeReadMetadataPool {
    typealias Record = [String: Any]
    private(set) var values = [Record]()
    private var indices = [Key: Int]()
    private var issues = [IssueKey: IssueEntry]()

    private enum IssueLeaf: Hashable {
        case text(String)
        case numeric(String, String)
        case boolean(Bool)
        case other(Key)
    }

    private enum IssueKey: Hashable {
        case message(detail: IssueLeaf, state: IssueLeaf)
        indirect case wrappedBatch(
            detail: IssueLeaf, code: IssueLeaf, domain: IssueLeaf, state: IssueLeaf, data: IssueLeaf, type: IssueLeaf
        )
        case other(Key)
    }
    private var fieldIssueTemplateCount = 0
    private var issueInt32 = [Int32: IssueLeaf]()

    private struct IssueEntry {
        let record: Record
        let fieldTemplate: NativeObservationEncoding.FieldIssueTemplate?
    }

    private static let maximumFieldIssueTemplates = 64
    private static let maximumFieldIssueTextBytes = 512
    private var batchIndices = [Int: Int]()
    private var fieldLists = [[String]: Record]()
    private var batchStatuses = [Int32: Record]()
    private var batchQueries = [Bool: Record]()
    private var relationScalars = [RelationScalar: Int]()
    private var relationTemplates = [RelationTemplate: NativeObservationEncoding.RelationReadTemplate]()
    private var relationSourceTemplates = [RelationSource: NativeObservationEncoding.RelationReadTemplate]()

    private enum SourceInteger: Hashable {
        case int(Int)
        case int64(Int64)
    }

    private enum RelationSource: Hashable {
        case page(
            api: String, complete: Bool, certainty: String, offset: SourceInteger, state: String, total: SourceInteger
        )
        case report(complete: Bool, certainty: String, detail: String, state: String, total: SourceInteger?)
    }

    private struct RelationDetail: Hashable {
        let name: String
        let reference: Int
    }

    private struct RelationTemplate: Hashable {
        let api: String?
        let complete: Bool
        let details: [RelationDetail]
    }

    // This is a request-local representation cache. Bound both its size and
    // the worst-case size of each retained, escaped JSON fragment.
    private static let maximumRelationTemplates = 64
    private static let maximumTemplateDetails = 16
    private static let maximumTemplateTextBytes = 64

    private enum RelationScalar: Hashable {
        case text(String), integer(Int), boolean(Bool)
    }

    private static let excludedRelationKeys: Set<String> = [
        "source", "property", "targets", "nativeType", "nativeAPI", "wrappedNativeValue", "relation"
    ]
    struct AttributeCatalogue {
        fileprivate let position: Int
        fileprivate let names: [String]
        fileprivate let value: Record?
    }
    private var attributeCatalogueCounts = [[String]: Int]()

    // Structural keys avoid encoding every repeated value to JSON merely to
    // discover equality. Numeric kinds remain distinct, including CFBoolean.
    private indirect enum Key: Hashable {
        case null
        case boolean(Bool)
        case number(String, String)
        case string(String)
        case array([Key])
        case object([Field])
        case nativeValue(String, Key)
        case nativeString(String, String)
        case nativeInteger(String, String)
        case nativeReference(String, String)
        case nativeItems(String, [Key])
        case nativeFields(String, [Field])
        case asciiAttributeCatalogue([String])
    }

    private static func nativeKey(_ value: Record) -> Key {
        guard value.count == 2, let type = value["nativeType"] as? String, let data = value["data"] else {
            return key(value)
        }
        if let text = data as? String { return .nativeString(type, text) }
        if let fields = data as? Record, fields.count == 1 {
            if let integer = fields["integer"] as? String { return .nativeInteger(type, integer) }
            if let reference = fields["ref"] as? String { return .nativeReference(type, reference) }
            if let items = fields["items"] as? [Record] {
                return .nativeItems(type, items.map(nativeKey))
            }
            if let entries = fields["fields"] as? [[Any]],
                entries.allSatisfy({ $0.count == 2 && $0[0] is String && $0[1] is Record })
            {
                return .nativeFields(type, entries.map {
                    Field(name: $0[0] as! String, value: nativeKey($0[1] as! Record))
                })
            }
        }
        return .nativeValue(type, key(data))
    }
    private struct Field: Hashable {
        let name: String
        let value: Key
    }

    private static func key(_ raw: Any) -> Key {
        // Most input here is a Swift value dictionary. Trying NSNumber first
        // would bridge every nested dictionary before failing the cast.
        if let fields = raw as? Record {
            return .object(fields.keys.sorted().map { Field(name: $0, value: key(fields[$0]!)) })
        }
        if let items = raw as? [Any] { return .array(items.map(key)) }
        if let string = raw as? String { return .string(string) }
        if raw is NSNull { return .null }
        if let number = raw as? NSNumber {
            if CFGetTypeID(number) == CFBooleanGetTypeID() { return .boolean(number.boolValue) }
            return .number(String(cString: number.objCType), number.stringValue)
        }
        preconditionFailure("Metadata contains a non-JSON native value")
    }

    private mutating func reference(_ value: Record, withKey exactKey: Key? = nil) -> Record {
        if value.count == 1, value["metadataValue"] is Int { return value }
        return ["metadataValue": referenceIndex(value, withKey: exactKey)]
    }

    private mutating func referenceIndex(_ value: Record, withKey exactKey: Key? = nil) -> Int {
        let key = exactKey ?? Self.nativeKey(value)
        if let index = indices[key] { return index }
        let index = values.count
        indices[key] = index
        values.append(value)
        return index
    }

    func resolved(_ reference: Record) -> Record? {
        guard reference.count == 1, let index = reference["metadataValue"] as? Int else { return nil }
        return values[index]
    }

    /// Error templates repeat across most unsupported fields. Share the raw
    /// status before expanding its typed nativeStatus representation, rather
    /// than constructing and hashing that whole representation for each field.
    mutating func encodeIssue(_ status: Record, construct: () -> Record) -> Record {
        issueEntry(status, shareField: false, construct: construct).record
    }

    mutating func encodeFieldIssue(
        _ status: Record, construct: () -> Record
    ) -> NativeObservationEncoding.IssueEvidence {
        let entry = issueEntry(status, shareField: true, construct: construct)
        if let template = entry.fieldTemplate { return .template(template) }
        return .raw([entry.record])
    }

    private mutating func issueEntry(
        _ status: Record, shareField: Bool, construct: () -> Record
    ) -> IssueEntry {
        let key = issueKey(status)
        if let existing = issues[key] { return existing }
        let record = encodeConstructedIssue(construct())
        var template: NativeObservationEncoding.FieldIssueTemplate?
        if shareField, fieldIssueTemplateCount < Self.maximumFieldIssueTemplates, Self.boundedIssue(record) {
            template = NativeObservationEncoding.FieldIssueTemplate(record)
            fieldIssueTemplateCount += 1
        }
        let entry = IssueEntry(record: record, fieldTemplate: template)
        issues[key] = entry
        return entry
    }

    private mutating func issueLeaf(_ value: Any) -> IssueLeaf {
        let type = Swift.type(of: value)
        if type == String.self, let text = value as? String { return .text(text) }
        if type == Int32.self, let code = value as? Int32 {
            if let cached = issueInt32[code] { return cached }
            let number = code as NSNumber
            let leaf: IssueLeaf = CFGetTypeID(number) == CFBooleanGetTypeID()
                ? .boolean(number.boolValue) : .numeric(String(cString: number.objCType), number.stringValue)
            if issueInt32.count < 32 { issueInt32[code] = leaf }
            return leaf
        }
        if (type == Int.self || type == Int32.self || type == Int64.self), let number = value as? NSNumber {
            if CFGetTypeID(number) == CFBooleanGetTypeID() { return .boolean(number.boolValue) }
            return .numeric(String(cString: number.objCType), number.stringValue)
        }
        let original = Self.key(value)
        switch original {
        case .string(let text): return .text(text)
        case .number(let type, let spelling): return .numeric(type, spelling)
        case .boolean(let value): return .boolean(value)
        default: return .other(original)
        }
    }

    private mutating func issueKey(_ status: Record) -> IssueKey {
        // Recognize only complete key sets. Every leaf keeps the structural
        // key's equality, so either spelling/bridge shares the first issue.
        if status.count == 2, let detail = status["detail"], let state = status["state"] {
            return .message(detail: issueLeaf(detail), state: issueLeaf(state))
        }
        if status.count == 5, let detail = status["detail"], let state = status["state"],
            let code = status["nativeCode"], let domain = status["nativeDomain"],
            let wrapped = status["wrappedNativeValue"] as? Record, wrapped.count == 2,
            let data = wrapped["data"], let type = wrapped["nativeType"]
        {
            return .wrappedBatch(
                detail: issueLeaf(detail), code: issueLeaf(code), domain: issueLeaf(domain),
                state: issueLeaf(state), data: issueLeaf(data), type: issueLeaf(type))
        }
        return .other(Self.key(status))
    }

    private static func boundedIssue(_ issue: Record) -> Bool {
        guard issue["reason"] != nil, issue["nativeStatus"] != nil else { return false }
        var remaining = maximumFieldIssueTextBytes
        for (name, value) in issue {
            switch name {
            case "reason", "detail":
                guard let text = value as? String, text.utf8.count <= remaining else { return false }
                remaining -= text.utf8.count
            case "nativeStatus", "continuation":
                guard let reference = value as? Record, reference.count == 1,
                    let index = reference["metadataValue"], Swift.type(of: index) == Int.self
                else { return false }
            default: return false
            }
        }
        return true
    }

    private mutating func encodeConstructedIssue(_ original: Record) -> Record {
        var issue = original
        for name in ["nativeStatus", "continuation"] {
            if let value = original[name] as? Record { issue[name] = reference(value) }
        }
        return issue
    }

    mutating func batchOwnerReference(_ owner: String) -> Int {
        referenceIndex(NativeObservationEncoding.value("AXUIElement", ["ref": owner]),
                       withKey: .nativeReference("AXUIElement", owner))
    }

    mutating func publishBatch(
        owner: Int, index: Int, issues: NativeObservationEncoding.IssueEvidence?
    ) -> NativeObservationEncoding.Read {
        let indexReference: Int
        if let existing = batchIndices[index] {
            indexReference = existing
        } else {
            indexReference = referenceIndex(NativeObservationEncoding.scalar(index))
            batchIndices[index] = indexReference
        }
        let published: NativeObservationEncoding.IssueEvidence?
        switch issues {
        case .template: published = issues
        case .raw(let records): published = .raw(records.map { encodeConstructedIssue($0) })
        case nil: published = nil
        }
        return .batch(.init(owner: owner, position: indexReference, issues: published))
    }

    private mutating func fieldListReference(_ fields: [String]) -> Record {
        // This key has the same ordering and String equality as the array's native key.
        if let reference = fieldLists[fields] { return reference }
        let pooled = reference(NativeObservationEncoding.scalar(fields))
        fieldLists[fields] = pooled
        return pooled
    }

    mutating func encodeBatchNodeRead(fields: [String], status: Int32, queried: Bool, requested: [String]) -> Record {
        // Match the original order of the three native detail values,
        // followed by the appended requested field list.
        let fieldReference = fieldListReference(fields)
        let statusReference: Record
        if let existing = batchStatuses[status] {
            statusReference = existing
        } else {
            statusReference = reference(NativeObservationEncoding.scalar(status))
            if batchStatuses.count < 32 { batchStatuses[status] = statusReference }
        }
        let queriedReference: Record
        if let existing = batchQueries[queried] {
            queriedReference = existing
        } else {
            queriedReference = reference(NativeObservationEncoding.scalar(queried))
            batchQueries[queried] = queriedReference
        }
        let requestedReference = fieldListReference(requested)
        return ["api": "AXUIElementCopyMultipleAttributeValues", "details": [
            ["batch_fields", fieldReference], ["batch_status", statusReference], ["queried", queriedReference],
            ["requested_fields", requestedReference],
        ] as [[Any]]]
    }

    private mutating func relationReference(_ raw: Any) -> Int {
        let type = Swift.type(of: raw)
        let key: RelationScalar
        if type == String.self, let text = raw as? String, text.utf8.allSatisfy({ $0 < 128 }) {
            key = .text(text)
        } else if type == Int.self, let integer = raw as? Int {
            key = .integer(integer)
        } else if type == Bool.self, let boolean = raw as? Bool {
            key = .boolean(boolean)
        } else {
            return referenceIndex(NativeObservationEncoding.scalar(raw))
        }
        if let existing = relationScalars[key] { return existing }
        let pooled = referenceIndex(NativeObservationEncoding.scalar(raw))
        relationScalars[key] = pooled
        return pooled
    }

    private static func templateText(_ value: String) -> Bool {
        value.utf8.count <= maximumTemplateTextBytes && value.utf8.allSatisfy { $0 < 128 }
    }

    private static func relationTemplate(_ read: Record, details: [RelationDetail]) -> RelationTemplate? {
        guard let rawComplete = read["complete"], Swift.type(of: rawComplete) == Bool.self,
            let complete = rawComplete as? Bool, details.count <= maximumTemplateDetails,
            details.allSatisfy({ templateText($0.name) })
        else { return nil }
        if read.count == 1 {
            return RelationTemplate(api: nil, complete: complete, details: details)
        }
        guard read.count == 2, let rawAPI = read["api"], Swift.type(of: rawAPI) == String.self,
            let api = rawAPI as? String, templateText(api)
        else { return nil }
        // The known header has no issues or unknown keys. ASCII admits exact
        // byte comparison with Swift String equality, including detail names.
        return RelationTemplate(api: api, complete: complete, details: details)
    }

    private static func sourceInteger(_ raw: Any?) -> SourceInteger? {
        guard let raw else { return nil }
        let type = Swift.type(of: raw)
        if type == Int.self, let value = raw as? Int { return .int(value) }
        if type == Int64.self, let value = raw as? Int64 { return .int64(value) }
        return nil
    }

    private static func sourceText(_ raw: Any?) -> String? {
        guard let raw, Swift.type(of: raw) == String.self, let text = raw as? String, templateText(text) else {
            return nil
        }
        return text
    }

    private static func relationSource(_ read: Record, _ source: Record) -> RelationSource? {
        guard let rawComplete = read["complete"], Swift.type(of: rawComplete) == Bool.self,
            let complete = rawComplete as? Bool, source["source"] != nil,
            source["property"] != nil, source["targets"] != nil,
            let certainty = sourceText(source["certainty"]), let state = sourceText(source["state"])
        else { return nil }
        // Match complete page/report shapes so unknown details never disappear.
        if read.count == 2, source.count == 9,
            let api = sourceText(read["api"]), source["nativeAPI"] != nil, source["nativeType"] != nil,
            let offset = sourceInteger(source["offset"]), let total = sourceInteger(source["total"])
        {
            return .page(api: api, complete: complete, certainty: certainty, offset: offset, state: state, total: total)
        }
        guard read.count == 1, (6...7).contains(source.count), let detail = sourceText(source["detail"]) else {
            return nil
        }
        let total = sourceInteger(source["total"])
        guard source.count == 6 || total != nil else { return nil }
        return .report(complete: complete, certainty: certainty, detail: detail, state: state, total: total)
    }

    mutating func encodeRelationRead(_ original: Record, source: Record) -> NativeObservationEncoding.Read {
        let sourceKey = Self.relationSource(original, source)
        // A hit reuses indices assigned at the first occurrence in this same
        // ordered pass. The metadata pool is append-only.
        if let sourceKey, let template = relationSourceTemplates[sourceKey] { return .relationTemplate(template) }
        let result = encodeRelationDetails(original, source: source)
        if let sourceKey, relationSourceTemplates.count < Self.maximumRelationTemplates,
            case .relationTemplate(let template) = result
        {
            relationSourceTemplates[sourceKey] = template
        }
        return result
    }

    private mutating func encodeRelationDetails(_ original: Record, source: Record) -> NativeObservationEncoding.Read {
        var keys = source.keys.filter { !Self.excludedRelationKeys.contains($0) }
        keys.append("relation")
        keys.sort()
        // Reserve indices at the original read position even on a template hit.
        let details = keys.map { name in
            RelationDetail(name: name, reference: relationReference(name == "relation" ? true : source[name]!))
        }
        let key = Self.relationTemplate(original, details: details)
        if let key, let template = relationTemplates[key] { return .relationTemplate(template) }
        var read = original
        read["details"] = details.map { [$0.name, ["metadataValue": $0.reference]] as [Any] }
        guard let key, relationTemplates.count < Self.maximumRelationTemplates else { return .record(read) }
        let template = NativeObservationEncoding.RelationReadTemplate(read)
        relationTemplates[key] = template
        return .relationTemplate(template)
    }

    mutating func encodeRead(_ original: Record, appendingRequestedFields fields: [String]? = nil) -> Record {
        var read = original
        let details = original["details"] as? [[Any]]
        if details != nil || fields != nil {
            var pooled = (details ?? []).map { pair -> [Any] in
                precondition(pair.count == 2)
                return [pair[0], reference(pair[1] as! Record)]
            }
            if let fields {
                pooled.append(["requested_fields", fieldListReference(fields)])
            }
            read["details"] = pooled
        }
        if let issues = original["issues"] as? [Record] {
            read["issues"] = issues.map { encodeConstructedIssue($0) }
        }
        return read
    }

    /// Only a complete ordered attribute-name result can share a property value.
    /// Restrict it to ASCII so Swift's canonical Unicode equality cannot merge
    /// differently encoded provider names.
    mutating func trackAttributeCatalogue(_ property: Record, at position: Int) -> AttributeCatalogue? {
        guard property.count == 2, property["key"] as? [String] == ["toolkit", "attribute_names"],
            let value = property["value"] as? Record, value.count == 2, value["nativeType"] as? String == "CFArray",
            let data = value["data"] as? Record, data.count == 1, let items = data["items"] as? [Record]
        else { return nil }
        var names = [String]()
        names.reserveCapacity(items.count)
        for item in items {
            guard item.count == 2, item["nativeType"] as? String == "CFString", let name = item["data"] as? String,
                name.utf8.allSatisfy({ $0 < 0x80 })
            else { return nil }
            names.append(name)
        }
        attributeCatalogueCounts[names, default: 0] += 1
        return AttributeCatalogue(position: position, names: names, value: value)
    }

    mutating func trackAttributeNames(_ names: [String], at position: Int) -> AttributeCatalogue {
        attributeCatalogueCounts[names, default: 0] += 1
        return AttributeCatalogue(position: position, names: names, value: nil)
    }

    mutating func sharedCatalogue(_ catalogue: AttributeCatalogue?) -> (position: Int, value: Record)? {
        guard let catalogue, attributeCatalogueCounts[catalogue.names, default: 0] > 1 else { return nil }
        // Keep this typed key separate from general read metadata. A Unicode
        // read detail can compare equal to ASCII under canonical equivalence.
        let key = Key.asciiAttributeCatalogue(catalogue.names)
        if let index = indices[key] { return (catalogue.position, ["metadataValue": index]) }
        let value = catalogue.value ?? NativeObservationEncoding.catalogueValue(catalogue.names)
        return (catalogue.position, reference(value, withKey: key))
    }

    mutating func encodeNode(_ original: Record, attributeCatalogue: AttributeCatalogue? = nil) -> Record {
        var node = original
        if let read = original["read"] as? Record { node["read"] = encodeRead(read) }
        if var properties = original["properties"] as? [Record] {
            if let shared = sharedCatalogue(attributeCatalogue) {
                properties[shared.position]["value"] = shared.value
            }
            node["properties"] = properties.map { original in
                var property = original
                if let read = original["read"] as? Record { property["read"] = encodeRead(read) }
                return property
            }
        }
        return node
    }
}
