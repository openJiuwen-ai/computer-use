import AppKit
import ApplicationServices

@MainActor enum NativeLaunchPipeline {
    static func execute(_ op: NativeOperation, step: Int64, command: NativeUnitCommand, access: NativeLaunchAccess)
        async throws -> [String: Any]
    {
        let payload = try JSONSerialization.data(
            withJSONObject: command.value, options: [.sortedKeys, .withoutEscapingSlashes])
        return try await op.execute(step: step, payload: payload) {
            await DispatchNativeLaunchPass.run(op, command: command, access: access)
        }
    }
}
