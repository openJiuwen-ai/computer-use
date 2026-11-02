// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

/// PREPARE: obtain the authorized exact document without a content mouse primer.
@MainActor enum PrepareActionDocumentForegroundPass {
    static func run(
        _ unit: any NativeActionDocumentInput, check: () throws -> Void, onPreparation: (String) -> Void,
        onCleanupUnknown: @escaping @MainActor () -> Void, onNativePreparation: (NativePreparationEffect) -> Void,
        onWait: (NativePreparationWait) -> Void
    ) async throws {
        try await unit.prepareForeground(
            check: check, onPreparation: onPreparation, onCleanupUnknown: onCleanupUnknown,
            onNativePreparation: onNativePreparation, onWait: onWait)
    }
}
