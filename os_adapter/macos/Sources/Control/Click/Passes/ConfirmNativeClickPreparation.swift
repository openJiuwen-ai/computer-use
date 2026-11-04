// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import Foundation

/// QUERY: preparation may change geometry but never the original target or semantic basis.
@MainActor enum ConfirmNativeClickPreparationPass {
    static func run(_ boundary: NativeUnitBoundary) throws {
        let metric = RequestDiagnostics.metrics?.start(.clickTargetValidation)
        defer { metric?.end() }
        // The immediately following target/readiness sample owns the live
        // scope query. Keep selection admission separate from that query.
        try boundary.checkSelection()
    }
}
