// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import ApplicationServices

/// QUERY only: one batch, or one individual getter per requested attribute when
/// the batch mechanism is unavailable. Both strategies consume the caller's
/// deadline; neither retries a getter or performs accessibility actions.
@MainActor enum AXAttributeReader {
    struct API {
        var batch: (AXUIElement, [String]) -> (AXError, CFArray?) = { element, fields in
            var values: CFArray?
            let status = AXUIElementCopyMultipleAttributeValues(element, fields as CFArray, [], &values)
            return (status, values)
        }
        var single: (AXUIElement, String) -> (AXError, CFTypeRef?) = { element, field in
            var value: CFTypeRef?
            let status = AXUIElementCopyAttributeValue(element, field as CFString, &value)
            return (status, value)
        }
    }
    struct Field {
        let name: String
        let value: CFTypeRef?
        let status: AXError
        let batchIndex: Int?
        let timedOut: Bool
    }
    struct Result {
        let status: AXError
        let queried: Bool
        let raw: CFArray?
        let fields: [Field]
    }

    static func read(_ element: AXUIElement, fields: [String], api: API = API(), withinBudget: () throws -> Bool) throws
        -> Result
    {
        let queried = try !fields.isEmpty && withinBudget()
        let (status, raw) =
            fields.isEmpty
            ? (AXError.success, nil as CFArray?) : queried ? api.batch(element, fields) : (.cannotComplete, nil)
        let values = raw as? [Any]
        let validBatch = status == .success && values?.count == fields.count
        let fallback =
            queried && !validBatch
            && (status == .success || status == .notImplemented || status == .cannotComplete || status == .failure)
        var result = [Field]()
        for (index, name) in fields.enumerated() {
            if validBatch {
                result.append(
                    Field(
                        name: name, value: values![index] as CFTypeRef, status: .success, batchIndex: index,
                        timedOut: false))
            } else if fallback, try withinBudget() {
                let (error, value) = api.single(element, name)
                result.append(
                    Field(
                        name: name, value: error == .success ? value : nil,
                        status: error == .success && value == nil ? .noValue : error, batchIndex: nil, timedOut: false))
            } else {
                result.append(
                    Field(
                        name: name, value: nil, status: status == .success ? .failure : status, batchIndex: index,
                        timedOut: !queried || fallback))
            }
        }
        return Result(status: status, queried: queried, raw: raw, fields: result)
    }
}
