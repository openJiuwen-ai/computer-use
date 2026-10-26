import AppKit
import ApplicationServices

/// QUERY/RESOURCE: collect the fixed window image through the original capture owner.
@MainActor enum CollectNativeWindowPixelsPass {
    static func run(_ request: NativeCaptureRequest, scopes: NativeScopeResolver) async throws -> NativeCapturedWindow {
        let binding = try scopes.windowTarget(request.target)
        let result = try await scopes.captures.capture(binding: binding, maxDimension: request.maxDimension)
        return NativeCapturedWindow(binding: binding, result: result)
    }
}
