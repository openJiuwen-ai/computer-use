// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import CoreFoundation
import Foundation

/// Write common observation records directly in the legacy Foundation JSON
/// key order. Fall back to the legacy serializer for other record shapes and
/// numeric forms so acquisition and diagnostic semantics are unchanged.
struct NativeObservationJSON {
    typealias Record = [String: Any]
    private var bytes: [UInt8]
    private var doubleTokens = [UInt64: Data]()
    private var readFragments = [ObjectIdentifier: Data]()
    private var batchIssueFragments = [ObjectIdentifier: Data]()
    init(prefix: [UInt8], capacity: Int = 4096) {
        bytes = prefix
        bytes.reserveCapacity(capacity)
    }

    mutating func encodeLineBody(
        _ observation: NativeObservationEncoding.Publication, maximumSize: Int
    ) throws -> [UInt8]? {
        try appendObservation(observation)
        return bytes.count <= maximumSize ? bytes : nil
    }

    private mutating func appendObservation(_ observation: NativeObservationEncoding.Publication) throws {
        defer {
            readFragments.removeAll()
            batchIssueFragments.removeAll()
        }
        // Write the established Foundation order. The metadata pool is
        // complete before any node references to its indices are emitted.
        literal("{\"kind\":\"observation\",\"metadataValues\":")
        try append(observation.metadataValues)
        literal(",\"nodes\":[")
        for (index, node) in observation.nodes.enumerated() {
            if index != 0 { bytes.append(44) }
            try appendNode(node)
        }
        literal("],\"platform\":\"macos\",\"read\":")
        try append(observation.read)
        literal(",\"roots\":")
        try append(observation.roots)
        literal(",\"session\":")
        try append(observation.session)
        bytes.append(125)
    }

    private mutating func appendNode(_ node: NativeObservationEncoding.PublishedNode) throws {
        if let resource = node.resource {
            try append(resource)
            return
        }
        literal("{\"id\":")
        text(node.id)
        literal(",\"kind\":\"element\",\"nativeType\":\"AXUIElement\",\"properties\":")
        if let properties = node.properties {
            bytes.append(91)
            for (index, property) in properties.enumerated() {
                if index != 0 { bytes.append(44) }
                try appendProperty(property)
            }
            bytes.append(93)
        } else {
            literal("null")
        }
        if let read = node.read {
            literal(",\"read\":")
            try append(read)
        }
        literal(",\"source\":\"macos.ax\"}")
    }

    private mutating func appendProperty(_ property: NativeObservationEncoding.Property) throws {
        switch property {
        case .catalogue:
            preconditionFailure("Unpublished attribute catalogue")
        case .raw(let value):
            try append(value)
        case .field(let field):
            literal("{\"key\":")
            propertyKey(namespace: field.namespace, name: field.name)
            if let read = field.read {
                literal(",\"read\":")
                try propertyRead(read)
            }
            if let state = field.state {
                literal(",\"state\":")
                text(state)
            }
            literal(",\"value\":")
            try appendCapturedValue(field.value)
            bytes.append(125)
        }
    }

    private mutating func propertyKey(namespace: String, name: String) {
        bytes.append(91)
        text(namespace)
        bytes.append(44)
        text(name)
        bytes.append(93)
    }

    private mutating func propertyRead(_ read: NativeObservationEncoding.Read) throws {
        switch read {
        case .record(let value):
            try append(value)
        case .relationTemplate(let template):
            try appendReadFragment(template.record, identity: ObjectIdentifier(template))
        case .issueTemplate(let template):
            try appendReadFragment(template.read, identity: ObjectIdentifier(template))
        case .acquiredBatch:
            preconditionFailure("Unpooled batch read")
        case .acquiredRelation:
            preconditionFailure("Unpooled relation read")
        case .batch(let batch):
            try appendBatchRead(batch)
        }
    }

    private mutating func appendBatchRead(_ batch: NativeObservationEncoding.BatchRead) throws {
        bytes.append(123)
        if batch.issues != nil { literal("\"complete\":false,") }
        literal("\"details\":[[\"batch_owner\",{\"metadataValue\":")
        unsigned(UInt64(batch.owner))
        literal("}],[\"batch_index\",{\"metadataValue\":")
        unsigned(UInt64(batch.position))
        literal("}]]")
        if let issues = batch.issues {
            literal(",\"issues\":")
            switch issues {
            case .raw(let records): try append(records)
            case .template(let template):
                let fragment = try batchIssueFragment(template)
                bytes.append(contentsOf: fragment)
            }
        }
        bytes.append(125)
    }

    private mutating func readFragment(_ read: Record, identity: ObjectIdentifier) throws -> Data {
        if let cached = readFragments[identity] { return cached }
        let fragment = try Self.fragment(read)
        readFragments[identity] = fragment
        return fragment
    }

    private mutating func appendReadFragment(_ read: Record, identity: ObjectIdentifier) throws {
        let fragment = try readFragment(read, identity: identity)
        bytes.append(contentsOf: fragment)
    }

    private mutating func batchIssueFragment(_ template: NativeObservationEncoding.FieldIssueTemplate) throws -> Data {
        let identity = ObjectIdentifier(template)
        if let cached = batchIssueFragments[identity] { return cached }
        let fragment = try Self.fragment(template.issues)
        batchIssueFragments[identity] = fragment
        return fragment
    }

    private static func fragment(_ value: Any) throws -> Data {
        var encoder = Self(prefix: [], capacity: 128)
        try encoder.append(value)
        return Data(encoder.bytes)
    }

    private mutating func literal(_ value: StaticString) {
        value.withUTF8Buffer { bytes.append(contentsOf: $0) }
    }

    private mutating func appendCapturedValue(_ value: NativeObservationEncoding.CapturedValue?) throws {
        switch value {
        case nil:
            literal("null")
        case .record(let record):
            try append(record)
        case .null:
            literal("{\"data\":null,\"nativeType\":\"CFNull\"}")
        case .boolean(let boolean):
            literal("{\"data\":")
            bytes.append(contentsOf: boolean ? Self.trueBytes : Self.falseBytes)
            literal(",\"nativeType\":\"CFBoolean\"}")
        case .string(let string):
            literal("{\"data\":")
            text(string)
            literal(",\"nativeType\":\"CFString\"}")
        case .point(let x, let y):
            try appendGeometry(first: ("x", x), second: ("y", y), tail: Self.pointTail)
        case .size(let width, let height):
            try appendGeometry(first: ("width", width), second: ("height", height), tail: Self.sizeTail)
        }
    }

    private mutating func appendGeometry(
        first: (String, Double), second: (String, Double), tail: [UInt8]
    ) throws {
        literal("{\"data\":{\"fields\":[")
        try appendCoordinate(name: first.0, number: first.1)
        bytes.append(44)
        try appendCoordinate(name: second.0, number: second.1)
        bytes.append(contentsOf: tail)
    }

    private static let pointTail = geometryTail("AXValue.CGPoint", NativeObservationEncoding.CapturedValue.screenMetadata)
    private static let sizeTail = geometryTail("AXValue.CGSize", NativeObservationEncoding.CapturedValue.sizeMetadata)

    private static func geometryTail(_ type: String, _ metadata: [(String, String)]) -> [UInt8] {
        var output = Self(prefix: [], capacity: 256)
        output.literal("]},\"metadata\":[")
        for (index, field) in metadata.enumerated() {
            if index != 0 { output.bytes.append(44) }
            output.bytes.append(91)
            output.text(field.0)
            output.literal(",{\"data\":")
            output.text(field.1)
            output.literal(",\"nativeType\":\"CFString\"}]")
        }
        output.literal("],\"nativeType\":")
        output.text(type)
        output.bytes.append(125)
        return output.bytes
    }

    private mutating func appendCoordinate(name: String, number: Double) throws {
        bytes.append(91)
        text(name)
        literal(",{\"data\":")
        try append(NativeObservationEncoding.floating(number))
        literal(",\"nativeType\":\"CGFloat\"}]")
    }

    private mutating func fallback(_ value: Any) throws {
        let encoded = try JSONSerialization.data(withJSONObject: value, options: [.sortedKeys, .fragmentsAllowed])
        bytes.append(contentsOf: encoded)
    }

    private mutating func append(_ value: Any, reuseDouble: Bool = true) throws {
        let concrete = Swift.type(of: value)
        if concrete == String.self {
            // The concrete type check above establishes the cast.
            // swiftlint:disable:next force_cast
            text(value as! String)
        } else if concrete == Int.self {
            // The concrete type check above establishes the cast.
            // swiftlint:disable:next force_cast
            let number = value as! Int
            if number < 0 { bytes.append(45) }
            unsigned(UInt64(number.magnitude))
        } else if concrete == Bool.self {
            // The concrete type check above establishes the cast.
            // swiftlint:disable:next force_cast
            bytes.append(contentsOf: (value as! Bool) ? Self.trueBytes : Self.falseBytes)
        } else if reuseDouble, concrete == Double.self, let number = value as? Double {
            let bits = number.bitPattern
            if let token = doubleTokens[bits] {
                bytes.append(contentsOf: token)
            } else {
                let start = bytes.count
                try append(value, reuseDouble: false)
                if doubleTokens.count < 512 { doubleTokens[bits] = Data(bytes[start...]) }
            }
        } else if concrete == [Record].self, let records = value as? [Record] {
            try appendRecords(records)
        } else if let map = value as? Record {
            try object(map, original: value)
        } else if let items = value as? [Any] {
            bytes.append(91)
            for (index, item) in items.enumerated() {
                if index != 0 { bytes.append(44) }
                try append(item)
            }
            bytes.append(93)
        } else if value is NSNull {
            bytes.append(contentsOf: Self.nullBytes)
        } else if let number = value as? NSNumber {
            if CFGetTypeID(number) == CFBooleanGetTypeID() {
                bytes.append(contentsOf: number.boolValue ? Self.trueBytes : Self.falseBytes)
            } else {
                switch UInt8(bitPattern: number.objCType.pointee) {
                case 99, 115, 105, 108, 113: // c s i l q
                    let signed = number.int64Value
                    if signed < 0 { bytes.append(45) }
                    unsigned(signed.magnitude)
                case 67, 83, 73, 76, 81: // C S I L Q
                    unsigned(number.uint64Value)
                default:
                    try fallback(value)
                }
            }
        } else {
            try fallback(value)
        }
    }

    private mutating func appendRecords(_ records: [Record]) throws {
        bytes.append(91)
        for (index, value) in records.enumerated() {
            if index != 0 { bytes.append(44) }
            try object(value, original: value)
        }
        bytes.append(93)
    }

    private mutating func object(_ value: Record, original: Any) throws {
        if value.count == 2, let data = value["data"], let type = value["nativeType"] {
            literal("{\"data\":")
            try append(data)
            literal(",\"nativeType\":")
            try append(type)
            bytes.append(125)
            return
        }
        if value.count == 3, let data = value["data"], let metadata = value["metadata"],
            let type = value["nativeType"]
        {
            literal("{\"data\":")
            try append(data)
            literal(",\"metadata\":")
            try append(metadata)
            literal(",\"nativeType\":")
            try append(type)
            bytes.append(125)
            return
        }
        if value.count == 1, let (name, item) = value.first {
            bytes.append(123)
            text(name)
            bytes.append(58)
            try append(item)
            bytes.append(125)
            return
        }
        if value.isEmpty {
            bytes.append(contentsOf: Self.emptyObjectBytes)
            return
        }
        if let keys = Self.keys(value) {
            bytes.append(123)
            for (index, key) in keys.enumerated() {
                if index != 0 { bytes.append(44) }
                text(key)
                bytes.append(58)
                try append(value[key]!)
            }
            bytes.append(125)
        } else {
            try fallback(original)
        }
    }

    private mutating func unsigned(_ number: UInt64) {
        withUnsafeTemporaryAllocation(of: UInt8.self, capacity: 20) { buffer in
            var remaining = number
            var index = buffer.count
            repeat {
                index -= 1
                buffer[index] = UInt8(remaining % 10) + 48
                remaining /= 10
            } while remaining != 0
            bytes.append(contentsOf: UnsafeBufferPointer(rebasing: buffer[index...]))
        }
    }

    private mutating func text(_ string: String) {
        bytes.append(34)
        let appended = string.utf8.withContiguousStorageIfAvailable { storage -> Bool in
            if storage.contains(where: { $0 < 32 || $0 == 34 || $0 == 47 || $0 == 92 }) {
                return false
            }
            bytes.append(contentsOf: storage)
            return true
        } ?? false
        if !appended {
            for byte in string.utf8 {
                switch byte {
                case 34, 47, 92:
                    bytes.append(92)
                    bytes.append(byte)
                case 8:
                    bytes.append(contentsOf: Self.backspaceBytes)
                case 9:
                    bytes.append(contentsOf: Self.tabBytes)
                case 10:
                    bytes.append(contentsOf: Self.linefeedBytes)
                case 12:
                    bytes.append(contentsOf: Self.formfeedBytes)
                case 13:
                    bytes.append(contentsOf: Self.returnBytes)
                case 0..<32:
                    bytes.append(contentsOf: Self.controlPrefix)
                    bytes.append(Self.hexDigits[Int(byte >> 4)])
                    bytes.append(Self.hexDigits[Int(byte & 15)])
                default:
                    bytes.append(byte)
                }
            }
        }
        bytes.append(34)
    }

    private static let nullBytes: [UInt8] = [110, 117, 108, 108]
    private static let trueBytes: [UInt8] = [116, 114, 117, 101]
    private static let falseBytes: [UInt8] = [102, 97, 108, 115, 101]
    private static let emptyObjectBytes: [UInt8] = [123, 125]
    private static let backspaceBytes: [UInt8] = [92, 98]
    private static let tabBytes: [UInt8] = [92, 116]
    private static let linefeedBytes: [UInt8] = [92, 110]
    private static let formfeedBytes: [UInt8] = [92, 102]
    private static let returnBytes: [UInt8] = [92, 114]
    private static let controlPrefix: [UInt8] = [92, 117, 48, 48]
    private static let hexDigits = Array("0123456789abcdef".utf8)

    // Each list is the exact Foundation .sortedKeys order for a complete
    // schema record. The fast path never infers order for unfamiliar keys.
    private static let candidates: [[[String]]] = [
        [], [],
        [
            ["data", "nativeType"], ["complete", "issues"], ["complete", "details"], ["api", "details"],
            ["key", "value"], ["nativeStatus", "reason"]
        ],
        [
            ["key", "read", "value"], ["detail", "nativeStatus", "reason"], ["complete", "details", "issues"],
            ["data", "metadata", "nativeType"], ["api", "complete", "details"], ["arguments", "key", "value"]
        ],
        [
            ["key", "read", "state", "value"], ["arguments", "key", "read", "value"],
            ["api", "complete", "details", "issues"], ["continuation", "detail", "nativeStatus", "reason"]
        ],
        [["id", "kind", "nativeType", "properties", "source"]],
        [["id", "kind", "nativeType", "properties", "read", "source"]],
        [
            ["kind", "metadataValues", "nodes", "platform", "read", "roots", "session"],
            ["api", "cached", "complete", "details", "intervalNs", "issues", "producer"]
        ],
    ]

    private static func keys(_ value: Record) -> [String]? {
        if value.count >= candidates.count { return nil }
        for shape in candidates[value.count] {
            if shape.allSatisfy({ value[$0] != nil }) { return shape }
        }
        return nil
    }
}
