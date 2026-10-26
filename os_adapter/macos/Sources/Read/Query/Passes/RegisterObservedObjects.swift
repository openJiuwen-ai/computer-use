import AppKit
import ApplicationServices

/// RESOURCE: borrowed query stage; failures stop publication and do not dispatch input.
@MainActor enum RegisterObservedObjectsPass {
    static func run<Scope, Facts>(_ scope: Scope, facts: Facts, register: (Scope, Facts) throws -> Void) throws {
        try register(scope, facts)
    }
}
