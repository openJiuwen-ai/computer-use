// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import Foundation

/// Exact old resolve/collect/final-check/register order; no extra RPC or retry.
@MainActor enum NativeQueryPipeline {
    static func collect<Scope, Facts>(
        resolveQueryScope: () throws -> Scope, collectRequestedFacts: (Scope) throws -> Facts,
        checkQueryBoundary: (Scope) throws -> Void, registerObservedObjects: (Scope, Facts) throws -> Void
    ) throws -> Facts {
        let scope = try ResolveQueryScopePass.run(resolveQueryScope)
        let facts = try CollectRequestedFactsPass.run(scope, collect: collectRequestedFacts)
        try CheckQueryBoundaryPass.run(scope, check: checkQueryBoundary)
        try RegisterObservedObjectsPass.run(scope, facts: facts, register: registerObservedObjects)
        return facts
    }
}
