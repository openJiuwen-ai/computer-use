// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices

/// One exact cell's preparation plan; no requested text or alternate target.
@MainActor struct NativeCellEditorPreparationPlan {
    let application: NSRunningApplication
    let window: AXUIElement
    let handoff: NativeCellEditorHandoff
    let pointer: NativeElementPointerUnit
    let activation: NativePointerIntent
}
