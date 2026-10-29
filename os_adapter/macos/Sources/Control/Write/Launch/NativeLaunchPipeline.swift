// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices

@MainActor enum NativeLaunchPipeline {
    static func execute(_ op: NativeOperation, step: Int64, command: NativeUnitCommand, access: NativeLaunchAccess)
        async throws -> [String: Any]
    {
        let payload = try JSONSerialization.data(
            withJSONObject: command.value, options: [.sortedKeys, .withoutEscapingSlashes])
        return try await op.execute(step: step, payload: payload) {
            let activity = NativeInputActivity(
                operation: op.token, step: step, command: command.kind, intent: op.request.kind, target: command.target,
                mode: BackgroundTarget.desktopMode)
            return await NativeInputActivity.$current.withValue(activity) {
                let result = await DispatchNativeLaunchPass.run(op, command: command, access: access)
                activity.finish(
                    dispatch: result["dispatch"] as? String ?? "unknown",
                    cleanup: result["cleanup"] as? String ?? "unknown")
                return result
            }
        }
    }
}
