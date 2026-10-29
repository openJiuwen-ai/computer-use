// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices

/// QUERY: borrowed query stage; failures stop publication and do not dispatch input.
@MainActor enum CheckQueryBoundaryPass {
    static func run<Scope>(_ scope: Scope, check: (Scope) throws -> Void) throws { try check(scope) }
}
