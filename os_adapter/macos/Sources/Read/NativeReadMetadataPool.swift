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
    private var issues = [Key: Record]()
    struct AttributeCatalogue {
        fileprivate let position: Int
        fileprivate let names: [String]
        fileprivate let value: Record
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
        let key = exactKey ?? Self.nativeKey(value)
        if let index = indices[key] { return ["metadataValue": index] }
        let index = values.count
        indices[key] = index
        values.append(value)
        return ["metadataValue": index]
    }

    func resolved(_ reference: Record) -> Record? {
        guard reference.count == 1, let index = reference["metadataValue"] as? Int else { return nil }
        return values[index]
    }

    /// Error templates repeat across most unsupported fields. Share the raw
    /// status before expanding its typed nativeStatus representation, rather
    /// than constructing and hashing that whole representation for each field.
    mutating func encodeIssue(_ status: Record, construct: () -> Record) -> Record {
        let key = Self.key(status)
        if let existing = issues[key] { return existing }
        let encoded = encodeConstructedIssue(construct())
        issues[key] = encoded
        return encoded
    }

    private mutating func encodeConstructedIssue(_ original: Record) -> Record {
        var issue = original
        for name in ["nativeStatus", "continuation"] {
            if let value = original[name] as? Record { issue[name] = reference(value) }
        }
        return issue
    }

    mutating func encodeRead(_ original: Record) -> Record {
        var read = original
        if let details = original["details"] as? [[Any]] {
            read["details"] = details.map { pair -> [Any] in
                precondition(pair.count == 2)
                return [pair[0], reference(pair[1] as! Record)]
            }
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

    mutating func encodeNode(_ original: Record, attributeCatalogue: AttributeCatalogue? = nil) -> Record {
        var node = original
        if let read = original["read"] as? Record { node["read"] = encodeRead(read) }
        if var properties = original["properties"] as? [Record] {
            if let catalogue = attributeCatalogue, attributeCatalogueCounts[catalogue.names, default: 0] > 1 {
                // Keep this typed key separate from general read metadata. For
                // example, an arbitrary Unicode read detail can compare equal
                // to an ASCII name under Swift String's canonical equality.
                properties[catalogue.position]["value"] = reference(
                    catalogue.value, withKey: .asciiAttributeCatalogue(catalogue.names))
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
