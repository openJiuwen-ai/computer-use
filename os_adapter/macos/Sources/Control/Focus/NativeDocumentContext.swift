// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices

/// The original standard window's action receiver context, separate from an editor.
@MainActor protocol NativeActionDocumentInput {
    var foreground: Bool { get }
    func focus() throws -> NativeDocumentContext.Focus
    func select(
        requireSelection: Bool, check: () throws -> Void, onPreparation: (String) -> Void,
        onNativePreparation: (NativePreparationEffect) -> Void) throws
    func prepareForeground(
        check: () throws -> Void, onPreparation: (String) -> Void, onCleanupUnknown: @escaping @MainActor () -> Void,
        onNativePreparation: (NativePreparationEffect) -> Void, onWait: (NativePreparationWait) -> Void) async throws
}

/// Exact standard-document context; never focuses an editor or sends a gesture.
@MainActor struct NativeDocumentContext: NativeActionDocumentInput {
    enum Focus { case target, other, unavailable }
    let application: NSRunningApplication
    let window: AXUIElement
    var foreground: Bool { BackgroundTarget(application).isForeground }

    static func permitsDispatch(_ focus: Focus, prepared: Bool) -> Bool {
        focus == .target || (!prepared && focus == .unavailable)
    }

    func focus() throws -> Focus {
        try BackgroundTarget(application).validate(input: true)
        let root = AXUIElementCreateApplication(application.processIdentifier)
        AXUIElementSetMessagingTimeout(root, 0.1)
        guard let current = AXAccess.element(root, kAXFocusedWindowAttribute) else { return .unavailable }
        if CFEqual(current, window) { return .target }
        guard let expected = BackgroundEvents.nativeWindowID(window),
            let actual = BackgroundEvents.nativeWindowID(current)
        else { return .unavailable }
        return actual == expected ? .target : .other
    }

    func select(
        requireSelection: Bool = false, check: () throws -> Void, onPreparation: (String) -> Void,
        onNativePreparation: (NativePreparationEffect) -> Void
    ) throws {
        let root = AXUIElementCreateApplication(application.processIdentifier)
        var selected = false
        for (object, attribute, value): (AXUIElement, String, CFTypeRef) in [
            (window, kAXMainAttribute, kCFBooleanTrue), (window, kAXFocusedAttribute, kCFBooleanTrue),
            (root, kAXFocusedWindowAttribute, window)
        ] {
            if try focus() == .target, !requireSelection || selected { return }
            guard AXAccess.writable(object, attribute).value == true else { continue }
            try check()
            try BackgroundTarget(application).validate(input: true)
            let detail = "native document context " + attribute
            onPreparation(detail)
            if NativeForegroundRestorationScope.current == nil {
                // Explicit-action preparation may precede dispatch in another
                // RPC. Its operation owner still retains this first context.
                NativeForegroundResponsibility.current?.recordPreparation(
                    target: BackgroundTarget(application).capturedProcessIdentity)
            }
            onNativePreparation(
                .init(
                    kind: .windowSelection, phase: .willSend, detail: detail,
                    window: BackgroundEvents.nativeWindowID(window)))
            let status = AXMessaging.setAttributeValue(object, attribute as CFString, value)
            selected = true
            try Self.checkSelectionResult(status)
            onNativePreparation(
                .init(
                    kind: .windowSelection, phase: .returned, detail: detail,
                    window: BackgroundEvents.nativeWindowID(window)))
            if status == .invalidUIElement { throw DriverError("action document disappeared", code: .staleTarget) }
            try check()
        }
    }
    static func checkSelectionResult(_ status: AXError) throws {
        guard status != .cannotComplete else {
            throw DriverError(
                "action document selection returned before its effect was known; no requested action was sent",
                code: .executionUnknown, effect: .possible)
        }
    }

    func prepareForeground(
        check: () throws -> Void, onPreparation: (String) -> Void, onCleanupUnknown: @escaping @MainActor () -> Void,
        onNativePreparation: (NativePreparationEffect) -> Void, onWait: (NativePreparationWait) -> Void
    ) async throws {
        try check()
        let mapping = try NativeWindowUnit(application: application, window: window).mapping()
        var changed = false
        try await ForegroundAssist().prepare(
            BackgroundTarget(application), window: mapping.id,
            onAction: {
                if !changed {
                    onNativePreparation(
                        .init(
                            kind: .foregroundActivation, phase: .willSend,
                            detail: "action document foreground preparation", window: mapping.id))
                }
                changed = true
                onPreparation("action document foreground preparation")
            }, onWait: onWait, onCleanupUnknown: onCleanupUnknown)
        if changed {
            onNativePreparation(
                .init(
                    kind: .foregroundActivation, phase: .returned, detail: "action document foreground preparation",
                    window: mapping.id))
        }
        try check()
    }

}
