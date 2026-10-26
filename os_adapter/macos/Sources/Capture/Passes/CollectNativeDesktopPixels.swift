import AppKit
import ApplicationServices

/// QUERY/RESOURCE: retain the original desktop capture and its interval; no AX query.
@MainActor enum CollectNativeDesktopPixelsPass {
    static func run(_ request: NativeCaptureRequest, scopes: NativeScopeResolver) async throws -> [String: Any] {
        guard try scopes.directoryTarget(request.target) != nil else {
            throw DriverError("desktop capture needs this session's read scope", code: .invalidRequest)
        }
        let interval = CaptureInterval()
        var result = try await scopes.desktop().read(maxDimension: request.maxDimension)
        result["kind"] = "desktopCapture"
        result["binding"] = request.target
        result["observed"] = interval.observation()
        return result
    }
}
