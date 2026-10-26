import AppKit
import ApplicationServices

/// QUERY: recheck the fixed native target after the real desktop-lease suspension.
@MainActor enum CheckNativeResourceBoundaryPass {
    static func run(_ resolved: NativeResourceTarget, mode: String, access: NativeResourceAccess) throws -> [String:
        Any]
    {
        let initial = resolved.initial
        return resolved.installation
            ? try access.directory.installation(initial).wire
            : resolved.discovery
                ? initial
                : resolved.visual
                    ? try access.scopes.inputBinding(initial).wire
                    : resolved.windowScope
                        ? (mode == "read"
                            ? try access.scopes.readScope(initial).wire : try access.scopes.inputScope(initial).wire)
                        : mode == "read"
                            ? try access.scopes.observationTarget(initial).wire : try access.scopes.target(initial).wire

    }
}
