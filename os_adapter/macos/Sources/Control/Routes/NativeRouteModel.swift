// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices

enum SelectedNativeRoute {
    case prepare(@MainActor () async throws -> Void)
    case dispatch(@MainActor () async throws -> Bool)
}

struct NativeRouteContext {
    let command: NativeUnitCommand
    let operation: NativeOperation
    let resolved: ResolvedNativeTarget
    let boundary: NativeUnitBoundary
    let receipt: NativeExecutionReceipt
    let access: NativeControlAccess
}
