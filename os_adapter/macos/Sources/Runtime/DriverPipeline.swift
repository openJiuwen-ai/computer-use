import AppKit
import ApplicationServices

@MainActor enum DriverPipeline {
    static func validate(_ method: String) throws { try ValidateNativeMethodPass.run(method) }
    static func perform(
        _ method: String, _ raw: Any?, timeoutMs: Int, operation: @escaping (Any?) throws -> NativeOperation,
        resource: NativeResourceAccess, query: NativeQueryAccess, control: NativeControlAccess,
        launch: NativeLaunchAccess
    ) async throws -> [String: Any] {
        if ["inspect", "capture", "execute"].contains(method) {
            let op = try operation((raw as? [String: Any])?["operation"])
            let deadline = ResolveNativeRequestDeadlinePass.run(op, enclosing: RequestDiagnostics.deadline)
            return try await RequestDiagnostics.$deadline.withValue(deadline) {
                if method == "capture" {
                    let budget = CreateCaptureObservationBudgetPass.run(
                        deadline: deadline, startedAt: ProcessInfo.processInfo.systemUptime)
                    return try await RequestDiagnostics.$observationBudget.withValue(budget) {
                        try await self.performStep(
                            method, raw, timeoutMs: timeoutMs, operation: operation, resource: resource, query: query,
                            control: control, launch: launch)
                    }
                }
                return try await self.performStep(
                    method, raw, timeoutMs: timeoutMs, operation: operation, resource: resource, query: query,
                    control: control, launch: launch)
            }
        }
        return try await performStep(
            method, raw, timeoutMs: timeoutMs, operation: operation, resource: resource, query: query, control: control,
            launch: launch)

    }
    static func performStep(
        _ method: String, _ raw: Any?, timeoutMs: Int, operation: @escaping (Any?) throws -> NativeOperation,
        resource: NativeResourceAccess, query: NativeQueryAccess, control: NativeControlAccess,
        launch: NativeLaunchAccess
    ) async throws -> [String: Any] {
        switch method {
        case "begin": return try await NativeResourceScope.begin(raw, timeoutMs: timeoutMs, access: resource)
        case "capture": return try await CapturePipeline.collect(raw, operation: operation, scopes: resource.scopes)
        case "inspect": return try ReadPipeline.collect(raw, operation: operation, access: query)
        case "execute":
            return try await ControlPipeline.execute(raw, operation: operation, access: control, launch: launch)
        case "finish": return try await NativeResourceScope.finish(raw, operation: operation)
        default: throw DriverError("unknown native operation command", code: .invalidRequest)
        }
    }
}
