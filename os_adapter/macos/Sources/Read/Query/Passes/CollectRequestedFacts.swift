import AppKit
import ApplicationServices

/// QUERY/RESOURCE: borrowed query stage; failures stop publication and do not dispatch input.
@MainActor enum CollectRequestedFactsPass {
    static func run<Scope, Facts>(_ scope: Scope, collect: (Scope) throws -> Facts) throws -> Facts {
        try collect(scope)
    }
}
