// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices

/// PREPARE: optional local activity for the original window's process. This
/// never establishes key-window, pointer-hit or keyboard-receiver readiness.
@MainActor enum PrepareFocusApplicationContextPass {
    static func run(_ request: NativeFocusRequest) async throws {
        let checkOriginalScope: @MainActor () throws -> Void = {
            try request.check()
            try request.target.validate(input: true)
            guard request.window.pid == request.target.application.processIdentifier else {
                throw DriverError("focus window no longer belongs to the original process", code: .staleTarget)
            }
            try BackgroundEvents.requireSyntheticFocusRoute(window: request.window)
        }
        try checkOriginalScope()
        _ = try await NativeApplicationContextUnit(application: request.target.application).prepare(
            requirement: .optional,
            check: checkOriginalScope, onPreparation: request.onPreparation, onCleanupUnknown: request.onCleanupUnknown,
            onNativePreparation: request.onNativePreparation)
    }
}
