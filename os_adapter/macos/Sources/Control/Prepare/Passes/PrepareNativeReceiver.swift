// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices

/// PREPARE: execute exactly the selected preparation unit with its original checks.
@MainActor enum PrepareNativeReceiverPass {
    static func run(_ body: @MainActor () async throws -> Void) async throws { try await body() }
}
