import AppKit
import ApplicationServices

struct ResolvedNativeTarget {
    let contract: NativePrimitiveContract
    let binding: (wire: [String: Any], blocked: [String], node: String?)
}

@MainActor struct NativeWriteRequest {
    let operation: NativeOperation
    let step: Int64
    let command: NativeUnitCommand
}
