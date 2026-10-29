// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices

/// QUERY/RESOURCE: collect only the requested native facts using original services and registration timing.
@MainActor enum ReadCapturedNativeTextPass {
    static func run(_ request: NativeReadRequest, ref: String, offset: Int, length: Int, access: NativeQueryAccess)
        throws -> [String: Any]
    {
        let op = request.operation, requested = request.requested
        guard !op.writable, try access.scopes.directoryTarget(requested) != nil else {
            throw DriverError("captured text requires a session read scope", code: .invalidRequest)
        }
        // Read the retained string, never the current AX object. It
        // remains readable after its window/element has disappeared.
        let page = try access.objects.readText(ref, offset: offset, length: length)
        try op.check()
        return ["kind": "capturedText", "session": access.session, "textRef": ref, "page": page]
    }
}
