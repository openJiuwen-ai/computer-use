import AppKit
import ApplicationServices

@MainActor enum CapturePipeline {
    static func collect(_ raw: Any?, operation: (Any?) throws -> NativeOperation, scopes: NativeScopeResolver)
        async throws -> [String: Any]
    {
        let plan = try ResolveNativeCapturePass.run(raw, operation: operation)
        return try await plan.operation.observe {
            try scopes.sharing.checkAvailable()
            switch plan.request.kind {
            case .window:
                let captured = try await CollectNativeWindowPixelsPass.run(plan.request, scopes: scopes)
                return RegisterNativeCapturePointerPass.run(captured, observe: scopes.observePointer)
            case .desktop: return try await CollectNativeDesktopPixelsPass.run(plan.request, scopes: scopes)
            }
        }
    }
}
