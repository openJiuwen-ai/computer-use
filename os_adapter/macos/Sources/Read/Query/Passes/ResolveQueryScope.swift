import AppKit
import ApplicationServices

/// QUERY: borrowed query stage; failures stop publication and do not dispatch input.
@MainActor enum ResolveQueryScopePass {
    static func run<Scope>(_ resolve: () throws -> Scope) throws -> Scope { try resolve() }
}
