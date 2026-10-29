// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices

@MainActor enum NativePreparePipeline {
    static func run(
        _ body: @MainActor () async throws -> Void, boundary: NativeUnitBoundary, op: NativeOperation, id: String
    ) async throws {
        try await PrepareNativeReceiverPass.run(body)
        try CheckPreparedSelectionPass.run(boundary.checkSelection)
        try ConfirmNativePreparationPass.run(op, id: id)
    }
}
