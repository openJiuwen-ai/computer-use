// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit

/// PREPARE: select and confirm one original standard-document receiver. The
/// parent owns optional process activity, foreground authority and dispatch.
@MainActor enum NativeDocumentActionPreparationPipeline {
    static func run(
        _ unit: any NativeActionDocumentInput, foreground: Bool, authorizeForeground: () throws -> Void,
        check: () throws -> Void, onPreparation: (String) -> Void, onCleanupUnknown: @escaping @MainActor () -> Void,
        onNativePreparation: (NativePreparationEffect) -> Void, onWait: (NativePreparationWait) -> Void
    ) async throws {
        func record(_ event: NativePreparationEffect) {
            NativeForegroundRestorationScope.current?.record(event)
            onNativePreparation(event)
        }
        try check()
        let focus = try unit.focus()
        try check()
        // A retained obligation may already be satisfied by local activity or
        // the user. Verify that before authorizing any foreground preparation.
        if focus == .target { return }
        if foreground && !unit.foreground {
            try authorizeForeground()
            try await PrepareActionDocumentForegroundPass.run(
                unit, check: check, onPreparation: onPreparation, onCleanupUnknown: onCleanupUnknown,
                onNativePreparation: record, onWait: onWait)
        }
        try PrepareActionDocumentContextPass.run(
            unit, check: check, onPreparation: onPreparation, onNativePreparation: record)
        try await ConfirmActionDocumentContextPass.run(unit, check: check, onWait: onWait)
    }
}
