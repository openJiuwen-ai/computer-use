// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices

/// DISPATCH/RESOURCE: same original launch unit and near-dispatch checks; no alternate route or retry.
@MainActor enum DispatchNativeLaunchPass {
    static func run(_ op: NativeOperation, command: NativeUnitCommand, access: NativeLaunchAccess) async -> [String:
        Any]
    {
        let receipt = NativeExecutionReceipt(
            target: command.target, detail: "Application launch pending.", confirmation: "notRequested",
            onEffect: access.recordEffect)
        do {
            try access.sharing.checkAvailable()
            try op.check(write: true)
            guard
                let contract = access.contracts.primitiveContracts(op.token).first(where: {
                    $0.id == command.primitiveID
                }), case .launch(let installation) = contract.unit,
                try access.directory.installation(command.target).identity == installation
            else { throw DriverError("unknown or changed installation launch contract", code: .staleTarget) }
            let launch = try await NativeLaunchUnit(installation: installation, discovery: access.discovery).perform(
                check: {
                    try access.sharing.checkAvailable()
                    try op.check(write: true)
                    try RequestDiagnostics.checkCancellation()
                },
                onDispatch: {
                    try op.check(write: true)
                    try RequestDiagnostics.checkCancellation()
                    receipt.dispatch = "unknown"
                    op.recordInputDispatch()
                    access.recordEffect()
                })
            receipt.dispatch = launch.launched ? "dispatched" : "notDispatched"
            receipt.application = launch.application
            receipt.confirmation = "confirmed"
            receipt.detail =
                launch.launched
                ? "The selected application started; window and content readiness require an explicit read."
                : "The selected installation is already running; no launch or activation was sent."
        } catch { receipt.recordFailure(error) }
        return receipt.wire
    }
}
