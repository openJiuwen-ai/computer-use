// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices

@MainActor enum ControlPipeline {
    static func execute(
        _ raw: Any?, operation: (Any?) throws -> NativeOperation, access: NativeControlAccess,
        launch: NativeLaunchAccess
    ) async throws -> [String: Any] {
        try await NativeWritePipeline.execute(raw, operation: operation, access: access, launch: launch)
    }
}
