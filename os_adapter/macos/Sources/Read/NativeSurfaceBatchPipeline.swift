// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import Foundation

/// One RPC preserves the ordered live collection for each original application.
/// Each application uses the selected profile within the same operation deadline.
@MainActor enum NativeSurfaceBatchPipeline {
    static func collect(_ request: NativeReadRequest, applications: [String],
                        profile: NativeInspectQuery.SurfaceProfile, access: NativeQueryAccess) throws
        -> [String: Any]
    {
        let began = Int64(Date().timeIntervalSince1970 * 1000)
        var entries = [[String: Any]]()
        for application in applications {
            try request.operation.check()
            do {
                let result = try CollectNativeSurfacesPass.run(
                    request, application: application, profile: profile, access: access)
                entries.append(["application": application, "result": result])
            } catch let error as DriverError where error.effect == nil || error.effect == FailureEffect.none {
                // Cancellation/deadline terminates the whole request. A provider
                // failure remains evidence for only its original application.
                try request.operation.check()
                entries.append(["application": application, "error": [
                    "code": error.code.rawValue, "effect": "none", "target": "native request",
                    "detail": error.description, "next": error.next,
                ]])
            }
        }
        return [
            "kind": "surfaceBatch", "session": access.session, "platform": "macos", "entries": entries,
            "observed": ["id": UUID().uuidString, "startedAt": began,
                         "completedAt": Int64(Date().timeIntervalSince1970 * 1000)],
            "stopReasons": [],
        ]
    }
}
