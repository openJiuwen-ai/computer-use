// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import Foundation

/// PURE: reject legacy/unknown methods before parsing their payload or borrowing an operation.
enum ValidateNativeMethodPass {
    static func run(_ method: String) throws {
        guard NativeMethods.values.contains(method) else {
            throw DriverError(
                "method '\(method)' is not supported by protocol v6", code: .invalidRequest,
                next: "use begin, inspect, capture, execute and finish in this native session")
        }
    }
}
