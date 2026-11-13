// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import CoreFoundation
import Foundation

/// Reuse the direct observation body writer for ordinary JSON replies after
/// proving that the original whole-document encoder would admit the values.
enum NativeObservationLine {
    static func encode(
        _ observation: NativeObservationEncoding.Publication, id: Int64, session: String, version: Int,
        maximumBytes: Int = 64 << 20
    ) -> Data? {
        let limit = min(maximumBytes, 64 << 20)
        var proof = NativeObservationLineProof(remainingBytes: limit)
        guard proof.accepts(observation, session: session) else { return nil }
        do {
            var prefix = Array("{\"id\":".utf8)
            prefix.append(contentsOf: try JSONSerialization.data(withJSONObject: id, options: .fragmentsAllowed))
            prefix.append(contentsOf: ",\"result\":".utf8)
            let suffix = try JSONSerialization.data(
                withJSONObject: ["session": session, "version": version], options: [.sortedKeys, .fragmentsAllowed])
            var writer = NativeObservationJSON(prefix: prefix)
            // A large unframed reply still uses the original writer. This
            // local fast-path cap does not limit what the driver can send.
            guard var line = try writer.encodeLineBody(observation, maximumSize: limit - suffix.count - 1)
            else { return nil }
            line.append(44)
            line.append(contentsOf: suffix.dropFirst())
            line.append(10)
            return Data(line)
        } catch {
            // The whole-document encoder owns any failure and its original order.
            return nil
        }
    }
}

/// The direct writer handles proven JSON values. The whole-document
/// serializer still owns foreign or unsupported graphs.
private struct NativeObservationLineProof {
    typealias Encoding = NativeObservationEncoding
    typealias Record = Encoding.Record

    private var templates = [ObjectIdentifier: Int]()
    private var remainingBytes: Int

    init(remainingBytes: Int) { self.remainingBytes = remainingBytes }

    private mutating func reserve(_ bytes: Int) -> Bool {
        guard bytes <= remainingBytes else { return false }
        remainingBytes -= bytes
        return true
    }

    mutating func accepts(_ observation: Encoding.Publication, session: String) -> Bool {
        // Conservative syntax reservations cover the outer envelope and
        // statically written observation, node, property, and batch wrappers.
        guard reserve(256), acceptsString(session), accepts(observation.session), accepts(observation.roots),
            accepts(observation.read), accepts(observation.metadataValues)
        else { return false }
        return observation.nodes.allSatisfy { node in
            guard reserve(128) else { return false }
            if let resource = node.resource { return accepts(resource) }
            guard acceptsString(node.id), node.read.map({ accepts($0) }) != false else { return false }
            return node.properties?.allSatisfy { property in
                switch property {
                case .field(let field):
                    return reserve(64) && acceptsString(field.namespace) && acceptsString(field.name)
                        && field.state.map({ acceptsString($0) }) != false
                        && field.value.map({ accepts($0) }) != false && accepts(field.read)
                case .raw(let value): return reserve(1) && accepts(value)
                case .catalogue: return false
                }
            } != false
        }
    }

    private mutating func accepts(_ value: Encoding.CapturedValue) -> Bool {
        switch value {
        case .record(let record): return accepts(record)
        case .null, .boolean: return reserve(48)
        case .string(let text): return reserve(38) && acceptsString(text)
        // Two numbers (each at most the ordinary number reservation) and
        // the fixed geometry fields, type names, and coordinate metadata.
        case .point, .size: return reserve(2 * 512 + 512)
        }
    }

    private mutating func accepts(_ read: Encoding.Read?) -> Bool {
        switch read {
        case nil: return true
        case .record(let value): return accepts(value)
        case .relationTemplate(let template): return acceptsTemplate(template, record: template.record)
        case .issueTemplate(let template): return acceptsTemplate(template, record: template.read)
        case .batch(let batch):
            guard batch.owner >= 0, batch.position >= 0, reserve(192) else { return false }
            switch batch.issues {
            case nil: return true
            case .raw(let issues): return accepts(issues)
            case .template(let template): return acceptsTemplate(template, record: template.read)
            }
        case .acquiredBatch, .acquiredRelation: return false
        }
    }

    private mutating func acceptsTemplate(_ template: AnyObject, record: Record) -> Bool {
        let id = ObjectIdentifier(template)
        if let bytes = templates[id] { return reserve(bytes) }
        let before = remainingBytes
        guard accepts(record) else { return false }
        templates[id] = before - remainingBytes
        return true
    }

    private mutating func accepts(_ value: Any, remaining: Int = 128) -> Bool {
        guard remaining > 0 else { return false }
        let type = Swift.type(of: value)
        if type == String.self, let text = value as? String { return acceptsString(text) }
        if type == Int.self { return reserve(21) }
        if type == Bool.self { return reserve(5) }
        if type == Double.self, let number = value as? Double { return number.isFinite && reserve(512) }
        if type is AnyClass {
            if let number = value as? NSNumber { return acceptsNumber(number) }
            if let text = value as? NSString {
                return Self.validUTF16(text as CFString) && acceptsString(text as String)
            }
            return value is NSNull && reserve(4)
        }
        // Try only Swift value containers. Do not bridge a foreign graph to
        // discover its shape, and stop before deeply nested recursion.
        if let record = value as? Record {
            return reserve(2 * record.count + 2)
                && record.allSatisfy { acceptsString($0.key) && accepts($0.value, remaining: remaining - 1) }
        }
        if let items = value as? [Any] {
            return reserve(items.count + 2) && items.allSatisfy { accepts($0, remaining: remaining - 1) }
        }
        guard let number = value as? NSNumber else { return false }
        return acceptsNumber(number)
    }

    private mutating func acceptsNumber(_ number: NSNumber) -> Bool {
        guard number.doubleValue.isFinite else { return false }
        if CFGetTypeID(number) == CFBooleanGetTypeID() { return reserve(5) }
        switch UInt8(bitPattern: number.objCType.pointee) {
        case 99, 115, 105, 108, 113, 67, 83, 73, 76, 81: return reserve(21)
        default: return reserve(512)
        }
    }

    private mutating func acceptsString(_ text: String) -> Bool {
        guard reserve(text.utf8.count + 2), text.isContiguousUTF8 || Self.validUTF16(text as CFString) else {
            return false
        }
        // JSON escaping can expand one control byte to six bytes and a
        // four-byte scalar to two six-byte surrogate escapes.
        let extra = text.utf8.reduce(into: 0) { size, byte in
            switch byte {
            case 0..<32: size += 5
            case 34, 47, 92: size += 1
            case 128...255: size += 2
            default: break
            }
        }
        return reserve(extra)
    }

    private static func validUTF16(_ text: CFString) -> Bool {
        let count = CFStringGetLength(text)
        return CFStringGetBytes(text, CFRange(location: 0, length: count), CFStringBuiltInEncodings.UTF8.rawValue,
                                0, false, nil, 0, nil) == count
    }
}
