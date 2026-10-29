// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices

@MainActor struct NativeReadRequest {
    let params: [String: Any]
    let query: NativeInspectQuery
    let operation: NativeOperation
    let requested: [String: Any]
}
