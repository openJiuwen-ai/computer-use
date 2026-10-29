// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import Foundation

/// Evidence emitted at the native preparation side-effect boundary, separate
/// from diagnostic text and from requested-input progress.
struct NativePreparationEffect {
    enum Kind {
        case focusNotification, windowSelection
        case foregroundActivation, menuContext, viewport
    }
    enum Phase { case willSend, returned }
    let kind: Kind
    let phase: Phase
    let detail: String
    let window: UInt32?
}

enum NativePreparationWait: String { case notNeeded, satisfied, exhausted, unavailable }
