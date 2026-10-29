// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices

@MainActor enum NativeDispatchPipeline {
    static func run(_ body: @MainActor () async throws -> Bool, receipt: NativeExecutionReceipt) async throws {
        let alreadySatisfied = try await SendNativeUnitPass.run(body)
        CompleteNativeDispatchPass.run(alreadySatisfied: alreadySatisfied, receipt: receipt)
    }
}
