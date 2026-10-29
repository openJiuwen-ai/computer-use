// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices

/// QUERY/RESOURCE: collect only the requested native facts using original services and registration timing.
@MainActor enum CollectNativeRootsPass {
    static func run(_ request: NativeReadRequest, application: String, access: NativeQueryAccess) throws -> [String:
        Any]
    {
        let op = request.operation, requested = request.requested
        guard !op.writable, try access.scopes.directoryTarget(requested) != nil else {
            throw DriverError("application roots require a session read scope", code: .invalidRequest)
        }
        let result = try access.directory.collectRoots(application)
        try op.check()
        return result
    }
}
