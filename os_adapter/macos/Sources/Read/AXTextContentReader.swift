// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import ApplicationServices
import Foundation

/// Read short text whose provider exposes a character range but an empty AXValue.
/// Each getter consumes the collection budget; no selection or input is changed.
@MainActor enum AXTextContentReader {
    static let maximumUnits = 4096
    struct API {
        var names: (AXUIElement) -> (AXError, CFArray?) = { element in
            var names: CFArray?
            let status = AXUIElementCopyParameterizedAttributeNames(element, &names)
            return (status, names)
        }
        var string: (AXUIElement, CFRange) -> (AXError, CFTypeRef?) = { element, requested in
            var range = requested
            guard let parameter = AXValueCreate(.cfRange, &range) else { return (.failure, nil) }
            var value: CFTypeRef?
            let status = AXUIElementCopyParameterizedAttributeValue(
                element, kAXStringForRangeParameterizedAttribute as CFString, parameter, &value)
            return (status, value)
        }
        var count: (AXUIElement) -> (AXError, CFTypeRef?) = { element in
            var value: CFTypeRef?
            let status = AXUIElementCopyAttributeValue(element, kAXNumberOfCharactersAttribute as CFString, &value)
            return (status, value)
        }
    }

    private static func units(_ raw: Any?) -> Int? {
        guard let number = raw as? NSNumber, CFGetTypeID(number) != CFBooleanGetTypeID(),
            !["f", "d"].contains(String(cString: number.objCType)), number.int64Value > 0,
            number.int64Value <= maximumUnits
        else { return nil }
        return number.intValue
    }

    static func read(
        _ element: AXUIElement, values: [String: Any], failures: [String: Any], api: API = API(),
        withinBudget: () throws -> Bool
    ) throws -> [[String: Any]] {
        guard values["AXValue"] as? String == "", failures["AXValue"] == nil, failures["AXNumberOfCharacters"] == nil,
            values["AXSubrole"] as? String != kAXSecureTextFieldSubrole,
            values["AXSubrole"] is String
                || ["absent", "unsupported"].contains(
                    (failures["AXSubrole"] as? [String: Any])?["state"] as? String ?? ""),
            try withinBudget()
        else { return [] }
        let (countStatus, countRaw) = api.count(element)
        var countProperty: [String: Any] = [
            "key": ["toolkit", "text_character_count"],
            "arguments": [NativeObservationEncoding.scalar("AXNumberOfCharacters")], "value": NSNull(),
            "read": [
                "api": "AXUIElementCopyAttributeValue",
                "details": NativeObservationEncoding.details([
                    "attribute": "AXNumberOfCharacters", "native_status": countStatus.rawValue
                ])
            ]
        ]
        if countStatus == .success, let countRaw,
            CFGetTypeID(countRaw) == CFNumberGetTypeID() || CFGetTypeID(countRaw) == CFBooleanGetTypeID()
        {
            countProperty["value"] = NativeObservationEncoding.scalar(countRaw as! NSNumber)
        } else {
            countProperty["state"] = countStatus == .noValue ? "absent" : "error"
            countProperty["read"] = [
                "api": "AXUIElementCopyAttributeValue", "complete": false,
                "issues": [
                    NativeObservationEncoding.issue(
                        AXFactEncoding.status(countStatus == .success ? .failure : countStatus))
                ]
            ]
        }
        var captured = [countProperty]
        guard countStatus == .success, let count = units(countRaw), try withinBudget() else { return captured }
        let (namesStatus, names) = api.names(element)
        var catalogue: [String: Any] = [
            "key": ["toolkit", "parameterized_attribute_names"], "value": NSNull(),
            "read": ["api": "AXUIElementCopyParameterizedAttributeNames"]
        ]
        if namesStatus == .success, let names, let strings = names as? [String] {
            catalogue["value"] = NativeObservationEncoding.scalar(strings)
            guard strings.contains(kAXStringForRangeParameterizedAttribute) else { return captured + [catalogue] }
        } else {
            catalogue["state"] = "error"
            catalogue["read"] = [
                "api": "AXUIElementCopyParameterizedAttributeNames", "complete": false,
                "issues": [NativeObservationEncoding.issue(AXFactEncoding.status(namesStatus))]
            ]
            return captured + [catalogue]
        }
        captured.append(catalogue)
        guard try withinBudget() else { return captured }
        let (status, raw) = api.string(element, CFRange(location: 0, length: count))
        let argument = NativeObservationEncoding.value(
            "AXValue.CFRange",
            [
                "fields": [
                    ["location", NativeObservationEncoding.scalar(0)],
                    ["length", NativeObservationEncoding.scalar(count)]
                ]
            ], metadata: [["index_unit", NativeObservationEncoding.value("CFString", "utf16")]])
        var result: [String: Any] = [
            "key": ["macos.ax", "AXStringForRange"], "arguments": [argument], "value": NSNull()
        ]
        var details: [String: Any] = [
            "character_count_before": count, "character_count_before_status": countStatus.rawValue,
            "native_status": status.rawValue
        ]
        var issues = [[String: Any]]()
        let text = raw as? String
        if status == .success, let text {
            result["value"] = NativeObservationEncoding.scalar(text)
            if text.utf16.count != count {
                issues.append([
                    "reason": "changed", "detail": "parameterized text length differs from the requested UTF-16 range"
                ])
            }
            if try withinBudget() {
                let (afterStatus, afterRaw) = api.count(element)
                details["character_count_after_status"] = afterStatus.rawValue
                if let afterRaw, CFGetTypeID(afterRaw) == CFNumberGetTypeID() {
                    details["character_count_after"] = afterRaw as! NSNumber
                }
                if afterStatus != .success || units(afterRaw) != count {
                    issues.append([
                        "reason": "changed",
                        "detail": "character count was not stable during parameterized text capture"
                    ])
                }
            } else {
                issues.append([
                    "reason": "timeout", "detail": "collection budget exhausted before character count confirmation"
                ])
            }
        } else {
            result["state"] = status == .noValue ? "absent" : "error"
            issues.append(
                NativeObservationEncoding.issue(AXFactEncoding.status(status == .success ? .failure : status)))
        }
        result["read"] = [
            "api": "AXUIElementCopyParameterizedAttributeValue", "complete": issues.isEmpty,
            "details": NativeObservationEncoding.details(details), "issues": issues
        ]
        return captured + [result]
    }
}
