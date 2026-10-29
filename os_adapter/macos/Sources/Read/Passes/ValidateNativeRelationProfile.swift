// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices

/// QUERY/PURE: preserve availability and requested native profile validation before collecting facts.
@MainActor enum ValidateNativeRelationProfilePass {
    static func run(access: NativeQueryAccess, property: String, cursor: String?, limit: Int) throws {
        try access.sharing.checkAvailable()
        guard AXAttributeProfile.attribute(property) else {
            throw DriverError("invalid native AX relation name", code: .invalidRequest)
        }

    }
}
