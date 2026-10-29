// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices

/// QUERY: borrow original read operation and require its exact capture scope.
@MainActor enum ResolveNativeCapturePass {
    static func run(_ raw: Any?, operation: (Any?) throws -> NativeOperation) throws -> NativeCapturePlan {
        let request = try NativeCaptureRequest(raw)
        let op = try operation(request.operation)
        guard !op.writable, NSDictionary(dictionary: request.target).isEqual(to: op.target) else {
            throw DriverError("capture requires the operation's original read scope", code: .invalidRequest)
        }
        return NativeCapturePlan(request: request, operation: op)
    }
}
