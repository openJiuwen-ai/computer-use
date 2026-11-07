// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import Foundation
import ApplicationServices

/// A complete selection belongs to the currently observed text extent.
/// No content string equality, selected row or caret/focus flag substitutes for
/// this check. Platform readers retain their own text-offset units.
struct NativeTextSelection: Equatable {
    let count: Int
    let location: Int?
    let length: Int?
    var contiguous = true

    // The count field is a scalar native character extent, not a collection.
    // swiftlint:disable:next empty_count
    var full: Bool { count >= 0 && contiguous && location == 0 && length == count }

    /// Decode a live native sample. Keeping field acquisition explicit lets
    /// tests exercise provider errors and changes between reads without UI input.
    static func read(_ field: (String) -> AXRead<CFTypeRef>) -> Self? {
        guard let count = extent(field), let selection = readRanges(textUTF16Count: count, field),
            extent(field) == count
        else { return nil }
        return selection
    }

    /// Value and other text getters can change selection. Make range the last
    /// query when it supplies a first-input or restoration proof.
    static func readBeforeRanges(_ field: (String) -> AXRead<CFTypeRef>, allowValueFallback: Bool = true) -> Self? {
        guard let count = extent(field, allowValueFallback: allowValueFallback),
            extent(field, allowValueFallback: allowValueFallback) == count else { return nil }
        return readRanges(textUTF16Count: count, field)
    }

    private static func extent(_ field: (String) -> AXRead<CFTypeRef>, allowValueFallback: Bool = true) -> Int? {
        switch field(kAXNumberOfCharactersAttribute) {
        case .value(let raw):
            guard let number = raw as? NSNumber, CFGetTypeID(number) != CFBooleanGetTypeID(),
                let count = Int(number.stringValue), count >= 0
            else { return nil }
            return count
        case .unsupported, .unavailable(.noValue):
            guard allowValueFallback else { return nil }
            // AppKit AX/NSRange uses UTF-16, not grapheme/scalar counts.
            return (field(kAXValueAttribute).value as? String).map { ($0 as NSString).length }
        default: return nil
        }
    }

    static func readRanges(textUTF16Count count: Int, _ field: (String) -> AXRead<CFTypeRef>) -> Self? {
        guard count >= 0 else { return nil }
        func range(_ raw: CFTypeRef) -> CFRange? {
            guard CFGetTypeID(raw) == AXValueGetTypeID() else { return nil }
            var result = CFRange()
            return AXValueGetValue(unsafeDowncast(raw, to: AXValue.self), .cfRange, &result) ? result : nil
        }
        let selection: CFRange?
        switch field(kAXSelectedTextRangeAttribute) {
        case .value(let raw):
            guard let value = range(raw), value.location >= 0, value.length >= 0, value.location <= count,
                value.length <= count - value.location
            else { return nil }
            selection = value
        case .unsupported, .unavailable(.noValue): selection = nil
        default: return nil
        }
        var contiguous = true
        switch field(kAXSelectedTextRangesAttribute) {
        case .value(let raw):
            guard let ranges = raw as? [CFTypeRef] else { return nil }
            let decoded = ranges.compactMap(range)
            guard decoded.count == ranges.count else { return nil }
            contiguous =
                decoded.isEmpty
                || (decoded.count == 1 && decoded[0].location == selection?.location
                    && decoded[0].length == selection?.length)
        case .unsupported, .unavailable(.noValue): break
        default: return nil
        }
        return Self(count: count, location: selection?.location, length: selection?.length, contiguous: contiguous)
    }
}
