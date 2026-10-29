// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices

/// DISPATCH: run one selected native unit. It retains guard/mark/send and paired cleanup.
@MainActor enum SendNativeUnitPass {
    static func run(_ body: @MainActor () async throws -> Bool) async throws -> Bool { try await body() }
}
