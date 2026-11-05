// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit

/// One captured point whose window was exposed and whose local event endpoint
/// was fixed before returning the user's foreground. It is not a reusable
/// permission: the consuming gesture still checks current local activity,
/// key-window identity, geometry and the original recipient.
@MainActor final class NativePreparedPointerPoint {
    private let window: WindowGeometry.WindowMapping
    private let point: CGPoint
    private let recipient: NativePointerRecipient
    private let process: NativeProcessIdentity
    private var consumed = false

    init(
        window: WindowGeometry.WindowMapping, point: CGPoint, recipient: NativePointerRecipient,
        process: NativeProcessIdentity
    ) {
        self.window = window
        self.point = point
        self.recipient = recipient
        self.process = process
    }

    func consume(
        window: WindowGeometry.WindowMapping, point: CGPoint,
        currentProcess: (pid_t) -> NativeProcessIdentity? = NativeProcessIdentity.read
    ) throws -> NativePointerRecipient {
        guard !consumed else { throw DriverError("prepared point already consumed", code: .invalidRequest) }
        consumed = true
        guard window.id == self.window.id, window.pid == self.window.pid,
            window.frame == self.window.frame, point == self.point,
            recipient.canPrepareBackgroundPoint, currentProcess(window.pid) == process
        else { throw DriverError("prepared point no longer names its original event destination", code: .staleTarget) }
        return recipient
    }
}
