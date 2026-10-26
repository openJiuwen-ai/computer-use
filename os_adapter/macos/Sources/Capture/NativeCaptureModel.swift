import AppKit
import ApplicationServices

@MainActor struct NativeCapturePlan {
    let request: NativeCaptureRequest
    let operation: NativeOperation
}
@MainActor struct NativeCapturedWindow {
    let binding: [String: Any]
    let result: [String: Any]
}
