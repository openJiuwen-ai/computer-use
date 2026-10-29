// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices

/// RESOURCE: preserve the selected unit result, including no-op and uncertain failure evidence.
@MainActor enum CompleteNativeDispatchPass {
    static func run(alreadySatisfied: Bool, receipt: NativeExecutionReceipt) {
        if !alreadySatisfied { receipt.dispatch = "dispatched" }
    }
}
