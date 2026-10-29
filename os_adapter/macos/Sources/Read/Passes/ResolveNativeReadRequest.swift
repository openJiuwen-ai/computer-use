// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices

/// QUERY: decode a fixed query and borrow the existing operation; reject a changed scope before collection.
@MainActor enum ResolveNativeReadRequestPass {
    static func run(_ raw: Any?, operation: (Any?) throws -> NativeOperation) throws -> NativeReadRequest {
        let p = try object(raw, keys: ["operation", "target", "query"])
        let query = try NativeInspectQuery(p["query"])
        let op = try operation(p["operation"])
        try op.check()
        guard let requested = p["target"] as? [String: Any],
            query.observesNodes ? op.permitsObservation(requested) : op.permits(requested)
        else { throw DriverError("operation target changed", code: .staleTarget) }
        return NativeReadRequest(params: p, query: query, operation: op, requested: requested)
    }
}
