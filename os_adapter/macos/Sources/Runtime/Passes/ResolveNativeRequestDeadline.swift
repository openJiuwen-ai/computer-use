// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import Foundation

/// PURE: intersect the existing operation and request deadlines without restarting a budget.
@MainActor enum ResolveNativeRequestDeadlinePass {
    static func run(_ operation: NativeOperation, enclosing: TimeInterval?) -> TimeInterval {
        min(operation.deadline, enclosing ?? .infinity)
    }
}
