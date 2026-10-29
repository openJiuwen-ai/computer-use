// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices

/// PREPARE: send the selected process key-focus notification, never activation.
@MainActor enum PrepareSyntheticApplicationPass {
    static func run(_ request: NativeFocusRequest) throws {
        try send(
            window: request.window.id, make: SyntheticFocusEvents.keyFocus,
            beforeSend: {
                try request.check()
                try request.target.validate(input: true)
            }, onPreparation: request.onPreparation, onNativePreparation: request.onNativePreparation,
            post: {
                let activity = NativeInputActivity.begin(
                    device: .focus, transport: .process, mechanism: "processKeyFocusNotification",
                    pid: request.window.pid, window: request.window.id, event: $0, stage: .preparation)
                $0.postToPid(request.window.pid)
                activity?.returned()
            }, afterSend: request.check)
    }

    /// Narrow event port keeps the single-post and preparation progress contract
    /// testable without posting to a real application. Returned is not consumed.
    static func send(
        window: UInt32, make: () throws -> CGEvent, beforeSend: () throws -> Void, onPreparation: (String) -> Void,
        onNativePreparation: (NativePreparationEffect) -> Void, post: (CGEvent) -> Void, afterSend: () throws -> Void
    ) throws {
        let focus = try make()
        try beforeSend()
        onPreparation("process key-focus notification")
        onNativePreparation(
            .init(kind: .focusNotification, phase: .willSend, detail: "process key-focus notification", window: window))
        post(focus)
        onNativePreparation(
            .init(kind: .focusNotification, phase: .returned, detail: "process key-focus notification", window: window))
        try afterSend()
    }
}
