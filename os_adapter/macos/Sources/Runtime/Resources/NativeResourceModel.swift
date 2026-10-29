// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices

struct NativeResourceTarget {
    let initial: [String: Any]
    let releaseNode: () -> Void
    let installation: Bool
    let discovery: Bool
    let visual: Bool
    let windowScope: Bool
}

@MainActor struct NativeResourceAccess {
    let directory: NativeDirectory
    let scopes: NativeScopeResolver
    let sharing: WindowSharing
    let objects: NativeApplicationAccess
    let hasUnfinishedOperation: () -> Bool
    let register: (NativeOperation, NativeSelectionBasis, String, String) -> Void
    let cancelActive: () -> Void
}
