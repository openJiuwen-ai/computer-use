// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

/// PREPARE: select only the retained document; no editor focus or content input.
@MainActor enum PrepareActionDocumentContextPass {
    static func run(
        _ unit: any NativeActionDocumentInput, check: () throws -> Void, onPreparation: (String) -> Void,
        onNativePreparation: (NativePreparationEffect) -> Void
    ) throws {
        try unit.select(
            requireSelection: false, check: check, onPreparation: onPreparation,
            onNativePreparation: onNativePreparation)
    }
}
