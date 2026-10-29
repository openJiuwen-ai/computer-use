// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices

/// PREPARE: enter editing at the original qualified cell, then prove its newly
/// selected editor. The requested text has not been sent at this stage.
@MainActor enum NativeCellEditorPreparationPipeline {
    static func run(
        element: AXUIElement, application: NSRunningApplication, window: AXUIElement,
        state: NativeCellEditorState, foreground: Bool, check: @escaping @MainActor () throws -> Void,
        onPreparation: @escaping @MainActor (String) -> Void,
        onCleanupUnknown: @escaping @MainActor () -> Void
    ) async throws {
        try check()
        let plan = try CaptureCellEditorPreparationPass.run(element: element, application: application, window: window)
        try await PrepareCellEditorPointerPass.run(
            plan, foreground: foreground, check: check, onPreparation: onPreparation,
            onCleanupUnknown: onCleanupUnknown)
        var entered = false
        do {
            try await ActivateCellEditorPass.run(
                plan, check: check, onActivation: {
                    if !entered { entered = true; onPreparation("activate original cell editor") }
                }, onCleanupUnknown: onCleanupUnknown)
            for attempt in 0..<12 {
                try check()
                if try ConfirmCellEditorActivationPass.run(plan, owner: state) { return }
                if attempt < 11 { try await Task.sleep(for: .milliseconds(25)) }
            }
            throw DriverError("the original cell's editor activation is unconfirmed; no replacement text was sent",
                              code: .inputUnavailable, effect: .possible)
        } catch {
            // Once the activation gesture may have entered, the Gate cannot
            // replay it through another preparation or text mechanism.
            if entered {
                throw DriverError(
                    "cell editor preparation may have changed UI state; requested text was not sent: \(error)",
                    code: .inputUnavailable, effect: .possible)
            }
            throw error
        }
    }
}
