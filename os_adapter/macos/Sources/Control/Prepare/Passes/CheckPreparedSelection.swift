import AppKit
import ApplicationServices

/// QUERY: preserve the post-preparation semantic dependency check before recording readiness.
@MainActor enum CheckPreparedSelectionPass {
    static func run(_ check: @MainActor () throws -> Void) throws { try check() }
}
