// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices

/// QUERY: preserve initial boundary and selection checks before selecting the native route.
@MainActor enum CheckNativeWriteBoundaryPass {
    static func run(_ boundary: NativeUnitBoundary) throws {
        try boundary.checkConditions()
        try boundary.checkSelection()
    }
}
