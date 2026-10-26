import Foundation
import ApplicationServices

/// Session-owned opaque resources keep identity independently of a read graph.
@MainActor final class AXNativeValueResources {
    typealias Record = [String: Any]
    private var entries = [(value: CFTypeRef, node: Record)]()
    func existing(_ raw: CFTypeRef) -> Record? { entries.first { CFEqual($0.value, raw) }?.node }
    func node(_ id: String) -> Record? { entries.first { $0.node["id"] as? String == id }?.node }
    func refreshIdentity(_ id: String, identity: Record) -> Record {
        let index = entries.firstIndex { $0.node["id"] as? String == id }!
        // The retained native object and binding stay fixed. Only this new
        // capture's dependency IDs change after a child resource was evicted.
        entries[index].node["identity"] = identity
        return entries[index].node
    }
    func retain(_ raw: CFTypeRef, node: Record) {
        entries.append((raw, node))
        if entries.count > 2048 { entries.removeFirst() }
    }
}

/// Encode values before bridging erases their Core Foundation type or geometry.
/// Object registration is supplied by acquisition and never implies parentage.
@MainActor final class AXNativeValueEncoding {
    typealias Record = [String: Any]
    private let retained: AXNativeValueResources
    private(set) var resources = [Record]()

    init(resources: AXNativeValueResources = AXNativeValueResources()) { retained = resources }

    func encode(_ raw: CFTypeRef, reference: (AXUIElement) throws -> String) throws -> Record {
        try Task.checkCancellation()
        let type = CFGetTypeID(raw)
        if type == AXUIElementGetTypeID() {
            do {
                return NativeObservationEncoding.value(
                    "AXUIElement", ["ref": try reference(unsafeDowncast(raw, to: AXUIElement.self))])
            } catch {
                if error is CancellationError { throw error }
                try Task.checkCancellation()
                // The original proxy remains retained as an opaque resource
                // when an acquisition budget cannot bind it for execution.
                return resource(
                    raw, kind: "unknown", name: "AXUIElement", identity: nil,
                    issue: ["reason": "native_reference_unavailable", "detail": String(describing: error)])
            }
        }
        if type == CFNullGetTypeID() { return NativeObservationEncoding.value("CFNull", NSNull()) }
        if type == CFBooleanGetTypeID() {
            return NativeObservationEncoding.value("CFBoolean", (raw as! NSNumber).boolValue)
        }
        if type == CFNumberGetTypeID() {
            let number = unsafeDowncast(raw, to: CFNumber.self)
            let name = "CFNumber." + String(CFNumberGetType(number).rawValue)
            let scalar = raw as! NSNumber
            return NativeObservationEncoding.value(
                name,
                CFNumberIsFloatType(number)
                    ? NativeObservationEncoding.floating(scalar.doubleValue) : ["integer": scalar.stringValue])
        }
        if type == CFStringGetTypeID() { return string(unsafeDowncast(raw, to: CFString.self)) }
        if type == CFDataGetTypeID() {
            let data = raw as! Data
            return NativeObservationEncoding.value("CFData", ["bytes": data.base64EncodedString()])
        }
        if type == CFURLGetTypeID() {
            let url = unsafeDowncast(raw, to: CFURL.self)
            var value = NativeObservationEncoding.value("CFURL", CFURLGetString(url)! as String)
            if let base = CFURLGetBaseURL(url) {
                value["metadata"] = [["base", try encode(base, reference: reference)]]
            }
            return value
        }
        if type == CFDateGetTypeID() {
            return NativeObservationEncoding.value(
                "CFDate",
                NativeObservationEncoding.floating(CFDateGetAbsoluteTime(unsafeDowncast(raw, to: CFDate.self))),
                metadata: [
                    ["epoch", NativeObservationEncoding.value("CFString", "2001-01-01T00:00:00Z")],
                    ["unit", NativeObservationEncoding.value("CFString", "second")]
                ])
        }
        if type == AXValueGetTypeID() { return axValue(unsafeDowncast(raw, to: AXValue.self)) }
        if type == CFArrayGetTypeID() {
            let array = raw as! [Any]
            return NativeObservationEncoding.value(
                "CFArray", ["items": try array.map { try encode($0 as CFTypeRef, reference: reference) }])
        }
        if type == CFDictionaryGetTypeID() {
            let map = unsafeDowncast(raw, to: CFDictionary.self)
            let count = CFDictionaryGetCount(map)
            var keys = [UnsafeRawPointer?](repeating: nil, count: count)
            var values = [UnsafeRawPointer?](repeating: nil, count: count)
            CFDictionaryGetKeysAndValues(map, &keys, &values)
            let entries: [[Record]] = try zip(keys, values).map { key, value in
                let key = Unmanaged<CFTypeRef>.fromOpaque(key!).takeUnretainedValue()
                let value = Unmanaged<CFTypeRef>.fromOpaque(value!).takeUnretainedValue()
                return [try encode(key, reference: reference), try encode(value, reference: reference)]
            }
            return NativeObservationEncoding.value("CFDictionary", ["entries": entries])
        }
        if type == CFAttributedStringGetTypeID() {
            let text = unsafeDowncast(raw, to: CFAttributedString.self)
            let length = CFAttributedStringGetLength(text)
            var runs = [Record](), offset: CFIndex = 0
            while offset < length {
                var range = CFRange()
                let attributes = CFAttributedStringGetAttributes(text, offset, &range)!
                let attributesValue = try encode(attributes, reference: reference)
                runs.append(
                    NativeObservationEncoding.value(
                        "CFAttributedString.Run",
                        [
                            "fields": [
                                ["range", span(range, nativeType: "CFRange", unit: "utf16_code_unit")],
                                ["attributes", attributesValue]
                            ]
                        ]))
                guard range.length > 0 else { break }
                offset = range.location + range.length
            }
            return NativeObservationEncoding.value(
                "CFAttributedString",
                [
                    "fields": [
                        ["text", string(CFAttributedStringGetString(text)!)],
                        ["runs", NativeObservationEncoding.value("CFArray", ["items": runs])]
                    ]
                ])
        }
        if type == AXTextMarkerGetTypeID() {
            let marker = unsafeDowncast(raw, to: AXTextMarker.self)
            let data = Data(bytes: AXTextMarkerGetBytePtr(marker), count: AXTextMarkerGetLength(marker))
            return resource(
                raw, kind: "text_marker", name: "AXTextMarker",
                identity: NativeObservationEncoding.value("CFData", ["bytes": data.base64EncodedString()]))
        }
        if type == AXTextMarkerRangeGetTypeID() {
            let range = unsafeDowncast(raw, to: AXTextMarkerRange.self)
            let start = try encode(AXTextMarkerRangeCopyStartMarker(range), reference: reference)
            let end = try encode(AXTextMarkerRangeCopyEndMarker(range), reference: reference)
            return resource(
                raw, kind: "text_marker_range", name: "AXTextMarkerRange",
                identity: NativeObservationEncoding.value(
                    "AXTextMarkerRange", ["fields": [["start", start], ["end", end]]]))
        }
        return resource(raw, kind: "unknown", name: "CFTypeID.\(type)", identity: nil)
    }

    private func resource(_ raw: CFTypeRef, kind: String, name: String, identity: Record?, issue: Record? = nil)
        -> Record
    {
        if let previous = retained.existing(raw), let id = previous["id"] as? String {
            let current = identity.map { retained.refreshIdentity(id, identity: $0) } ?? previous
            expose(current, id: id)
            return NativeObservationEncoding.value(name, ["ref": id])
        }
        let id = "resource-" + UUID().uuidString
        var node: Record = ["id": id, "source": "macos.ax", "kind": kind, "nativeType": name, "properties": NSNull()]
        if let identity {
            node["identity"] = identity
        } else {
            node["read"] = [
                "complete": false,
                "issues": [issue ?? ["reason": "unsupported_native_type", "detail": "CFTypeID \(CFGetTypeID(raw))"]]
            ]
        }
        retained.retain(raw, node: node)
        expose(node, id: id)
        return NativeObservationEncoding.value(name, ["ref": id])
    }

    private func expose(_ node: Record, id: String) {
        guard !resources.contains(where: { $0["id"] as? String == id }) else { return }
        resources.append(node)
        func scan(_ raw: Any) {
            if let map = raw as? Record {
                if let target = map["ref"] as? String, let dependency = retained.node(target) {
                    expose(dependency, id: target)
                } else {
                    for value in map.values { scan(value) }
                }
            } else if let list = raw as? [Any] {
                for item in list { scan(item) }
            }
        }
        scan(node)
    }

    private func string(_ raw: CFString) -> Record {
        let text = raw as String
        var value = NativeObservationEncoding.value("CFString", text)
        var original = [UInt16](repeating: 0, count: CFStringGetLength(raw))
        CFStringGetCharacters(raw, CFRange(location: 0, length: original.count), &original)
        if original != Array(text.utf16) {
            let bytes = original.flatMap { [UInt8(truncatingIfNeeded: $0), UInt8(truncatingIfNeeded: $0 >> 8)] }
            value["raw"] = Data(bytes).base64EncodedString()
            value["metadata"] = [["raw_encoding", NativeObservationEncoding.value("CFString", "utf-16-le")]]
        }
        return value
    }

    private func span(_ range: CFRange, nativeType: String = "AXValue.CFRange", unit: String = "native_cf_range")
        -> Record
    {
        NativeObservationEncoding.value(
            nativeType,
            [
                "fields": [
                    ["location", NativeObservationEncoding.value("CFIndex", ["integer": String(range.location)])],
                    ["length", NativeObservationEncoding.value("CFIndex", ["integer": String(range.length)])]
                ]
            ], metadata: [["index_unit", NativeObservationEncoding.value("CFString", unit)]])
    }

    private func axValue(_ raw: AXValue) -> Record {
        func number(_ value: CGFloat) -> Record {
            NativeObservationEncoding.value("CGFloat", NativeObservationEncoding.floating(Double(value)))
        }
        let geometry: [[Any]] = [
            ["coordinate_space", NativeObservationEncoding.value("CFString", "macos.ax.global_screen")],
            ["unit", NativeObservationEncoding.value("CFString", "point")],
            ["coordinate_metadata_source", NativeObservationEncoding.value("CFString", "api_contract")]
        ]
        switch AXValueGetType(raw) {
        case .cgPoint:
            var point = CGPoint.zero
            if AXValueGetValue(raw, .cgPoint, &point) {
                return NativeObservationEncoding.value(
                    "AXValue.CGPoint", ["fields": [["x", number(point.x)], ["y", number(point.y)]]], metadata: geometry)
            }
        case .cgSize:
            var size = CGSize.zero
            if AXValueGetValue(raw, .cgSize, &size) {
                return NativeObservationEncoding.value(
                    "AXValue.CGSize", ["fields": [["width", number(size.width)], ["height", number(size.height)]]],
                    metadata: geometry.filter { $0[0] as? String != "coordinate_space" })
            }
        case .cgRect:
            var rect = CGRect.zero
            if AXValueGetValue(raw, .cgRect, &rect) {
                return NativeObservationEncoding.value(
                    "AXValue.CGRect",
                    [
                        "fields": [
                            ["x", number(rect.origin.x)], ["y", number(rect.origin.y)],
                            ["width", number(rect.size.width)], ["height", number(rect.size.height)]
                        ]
                    ], metadata: geometry)
            }
        case .cfRange:
            var range = CFRange()
            if AXValueGetValue(raw, .cfRange, &range) { return span(range) }
        case .axError:
            var error = AXError.failure
            if AXValueGetValue(raw, .axError, &error) {
                return NativeObservationEncoding.value("AXValue.AXError", error.rawValue)
            }
        default: break
        }
        return resource(raw, kind: "native_value", name: "AXValue.\(AXValueGetType(raw).rawValue)", identity: nil)
    }
}
