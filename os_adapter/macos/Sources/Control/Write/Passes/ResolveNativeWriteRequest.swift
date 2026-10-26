import AppKit
import ApplicationServices

/// QUERY/PURE: decode command and fixed operation; step dedup remains in NativeOperation before live target queries.
@MainActor enum ResolveNativeWriteRequestPass {
    static func run(_ raw: Any?, operation: (Any?) throws -> NativeOperation) throws -> NativeWriteRequest {
        let p = try object(raw, keys: ["operation", "step", "command"])
        let op = try operation(p["operation"])
        let n = try number(p["step"], label: "step")
        guard n > 0, n.rounded() == n, n <= 9_007_199_254_740_991 else {
            throw DriverError("invalid native step", code: .invalidRequest)
        }
        let decoded = try NativeUnitCommand(p["command"])
        let kind = decoded.kind, intentKind = op.request.kind
        let bound = decoded.target
        guard op.permits(bound), (kind == "launch_application") == (intentKind == "launch") else {
            throw DriverError("native object contract or target mismatch", code: .invalidRequest)
        }
        return NativeWriteRequest(operation: op, step: Int64(n), command: decoded)
    }
}
