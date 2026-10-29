// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import Foundation

/// PREPARE: qualify the original cell's pointer route under the selected mode.
@MainActor enum PrepareCellEditorPointerPass {
    static func run(
        _ plan: NativeCellEditorPreparationPlan, foreground: Bool,
        check: @escaping @MainActor () throws -> Void,
        onPreparation: @escaping @MainActor (String) -> Void,
        onCleanupUnknown: @escaping @MainActor () -> Void
    ) async throws {
        try await plan.pointer.prepare(
            foreground: foreground, check: check, onPreparation: onPreparation, onCleanupUnknown: onCleanupUnknown)
    }
}
