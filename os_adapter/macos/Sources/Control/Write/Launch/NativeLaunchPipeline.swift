// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices

@MainActor enum NativeLaunchPipeline {
    static func execute(_ op: NativeOperation, step: Int64, command: NativeUnitCommand, access: NativeLaunchAccess)
        async throws -> [String: Any]
    {
        let payload = try JSONSerialization.data(
            withJSONObject: command.value, options: [.sortedKeys, .withoutEscapingSlashes])
        return try await op.execute(step: step, payload: payload, candidateID: command.primitiveID) {
            let activity = NativeInputActivity(
                operation: op.token, step: step, command: command.kind, intent: op.request.kind, target: command.target,
                globalInputAllowed: NativeInputAuthorization.globalInputAllowed)
            return await NativeInputActivity.$current.withValue(activity) {
                let receipt = await DispatchNativeLaunchPass.run(op, command: command, access: access)
                receipt.impact = activity.finish(dispatch: receipt.dispatch, cleanup: receipt.cleanup)
                return receipt.wire
            }
        }
    }
}
