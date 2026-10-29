// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices

/// RESOURCE: record confirmation only on the original operation; no requested input is sent.
@MainActor enum ConfirmNativePreparationPass {
    static func run(_ operation: NativeOperation, id: String) throws { try operation.confirmPreparation(id) }
}
