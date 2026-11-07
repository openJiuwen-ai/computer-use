// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices

/// A preparation-only click on one confirmed native editor hit. Python chooses
/// this route after AX preparation; this unit never sends requested text/keys.
@MainActor struct NativePointerFocusUnit {
    enum TargetKind: Equatable { case editor, scalarText }

    static func supportsEditorPreparation(role: String?, intent: NativeKeyboardIntent.Kind) -> Bool {
        guard [.focus, .insert, .replace, .paste].contains(intent) else { return false }
        if role == kAXComboBoxRole { return intent == .focus }
        return [kAXTextFieldRole, kAXTextAreaRole, "AXWebArea"].contains(role ?? "")
    }
    /// Narrow native leaves for the one pointer-focus preparation sequence.
    /// Tests drive the same sequence without posting desktop input.
    struct PreparationAccess {
        let ready: @MainActor () throws -> Bool
        let selection: @MainActor () -> CFRange?
        let preparePointer: @MainActor () async throws -> Void
        let clickPointer: @MainActor () async throws -> Void
        let receives: @MainActor () throws -> Bool
        let selectionWritable: @MainActor () -> Bool
        let restoreSelection: @MainActor (CFRange) throws -> AXError
    }

    let application: NSRunningApplication
    let window: AXUIElement
    let element: AXUIElement
    var receiverRelation: NativeReceiverRelation? = nil
    var cellEditor: NativeCellEditorHandoff? = nil
    var targetKind: TargetKind = .editor

    private var pointer: NativeElementPointerUnit {
        NativeElementPointerUnit(application: application, window: window, element: element)
    }

    private var scalar: NativeScalarTextPoint {
        NativeScalarTextPoint(application: application, window: window, element: element)
    }

    private func scalarPointer(_ sample: NativeScalarTextPoint.Sample) -> NativePointerUnit {
        NativePointerUnit(
            application: application, window: sample.mapping, nativeWindow: window,
            pointerRequirement: .foregroundWindowPoint, recipientElement: element)
    }

    func backgroundPreparationAvailable() throws -> Bool {
        if targetKind == .scalarText {
            guard BackgroundEvents.capabilities.windowDirectedTransport, let sample = try scalar.sample() else {
                return false
            }
            return scalarPointer(sample).ready(at: sample.point, delivery: .windowDirected)
        }
        return try pointer.backgroundPreparationAvailable()
    }

    func readiness() throws -> NativeReadiness {
        if try NativeReceiverSnapshot.read(application: application, window: window, receiverRelation: receiverRelation)
            .receives(element)
        {
            if let cellEditor,
                try cellEditor.association(window: window).map({ CFEqual($0.editor, element) }) != true {
                return NativeReadiness([("receiver", "unknown")])
            }
            return NativeReadiness()
        }
        if targetKind == .scalarText {
            guard BackgroundEvents.capabilities.windowDirectedTransport, let sample = try scalar.sample() else {
                return NativeReadiness([("geometry", "unknown")], preparationAllowed: false)
            }
            let ready = scalarPointer(sample).ready(at: sample.point, delivery: .windowDirected)
            return NativeReadiness([("foreground", ready ? "yes" : "no"), ("receiver", "no")])
        }
        let facts = try pointer.conditions()
        return facts.readiness.awaitingReceiver()
    }

    private func selection() -> CFRange? {
        guard let value = AXAccess.read(element, kAXSelectedTextRangeAttribute).value,
            CFGetTypeID(value) == AXValueGetTypeID()
        else { return nil }
        var range = CFRange()
        return AXValueGetValue(unsafeDowncast(value, to: AXValue.self), .cfRange, &range) ? range : nil
    }

    func prepare(
        intent: NativeKeyboardIntent.Kind, foreground: Bool, check: @escaping @MainActor () throws -> Void,
        onPreparation: @escaping @MainActor (String) -> Void, onCleanupUnknown: @escaping @MainActor () -> Void
    ) async throws {
        if targetKind == .scalarText {
            guard intent == .replace else {
                throw DriverError("scalar text-point preparation only authorizes replacement", code: .invalidRequest)
            }
            try await prepareScalarTextPoint(
                foreground: foreground, check: check, onPreparation: onPreparation,
                onCleanupUnknown: onCleanupUnknown)
            return
        }
        let checkEffect: @MainActor () throws -> Void = {
            try check()
            try cellEditor?.requireAssociation(element: element, window: window)
        }
        let access = PreparationAccess(
            ready: { try readiness().ready }, selection: selection,
            preparePointer: {
                try await pointer.prepare(
                    foreground: foreground, check: check, onPreparation: onPreparation,
                    onCleanupUnknown: onCleanupUnknown)
            },
            clickPointer: {
                try checkEffect()
                let click = try NativePointerIntent(
                    unit: "pointer_sequence", kind: "click", parameters: ["button": "left", "count": 1],
                    foreground: false, element: true)
                try await pointer.deliver(
                    click, check: checkEffect, guardOperation: checkEffect,
                    onDispatch: { onPreparation("verified editor pointer focus") },
                    onPosted: {}, onCleanupUnknown: onCleanupUnknown)
            },
            receives: {
                try NativeReceiverSnapshot.read(
                    application: application, window: window, receiverRelation: receiverRelation).receives(element)
            }, selectionWritable: { AXAccess.writable(element, kAXSelectedTextRangeAttribute).value == true },
            restoreSelection: { range in
                var original = range
                guard let value = AXValueCreate(.cfRange, &original) else {
                    throw DriverError("cannot encode the editor selection", code: .inputUnavailable)
                }
                try checkEffect()
                onPreparation("native editor selection restoration")
                return AXMessaging.setAttributeValue(element, kAXSelectedTextRangeAttribute as CFString, value)
            })
        try await Self.prepare(intent: intent, access: access, check: check)
    }

    /// Compose one text-point preparation with the existing window-directed
    /// transport. The point is a live property of the original scalar editor,
    /// not a capture, a whole-element center, or an authority to edit its value.
    private func prepareScalarTextPoint(
        foreground: Bool, check: @escaping @MainActor () throws -> Void,
        onPreparation: @escaping @MainActor (String) -> Void, onCleanupUnknown: @escaping @MainActor () -> Void
    ) async throws {
        let checkEffect: @MainActor () throws -> Void = {
            try check()
            try cellEditor?.requireAssociation(element: element, window: window)
        }
        var selected: NativeScalarTextPoint.Sample?
        var clickStarted = false
        let access = PreparationAccess(
            ready: { try readiness().ready }, selection: { nil },
            preparePointer: {
                try checkEffect()
                guard BackgroundEvents.capabilities.windowDirectedTransport else {
                    throw NativePreparationUnavailable(
                        description: "window-directed scalar preparation is unavailable; no pointer event sent")
                }
                let initial = try scalar.requireSample()
                _ = try await scalarPointer(initial).prepareWindowContext(
                    foreground: foreground, atNative: initial.point, delivery: .windowDirected,
                    check: checkEffect, onPreparation: onPreparation, onCleanupUnknown: onCleanupUnknown)
                try checkEffect()
                let current = try scalar.requireSample()
                guard current.mapping.id == initial.mapping.id, current.mapping.pid == initial.mapping.pid else {
                    throw DriverError("the original scalar window changed during preparation", code: .staleTarget)
                }
                try scalarPointer(current).requirePointReadiness(atNative: current.point, delivery: .windowDirected)
                selected = current
                if WindowGeometry.inputDiagnosticsEnabled {
                    diagnose([
                        "kind": "scalar_text_point_preparation", "source": "AXBoundsForRange",
                        "targetPID": current.mapping.pid, "window": current.mapping.id,
                        "range": [current.boundsRange.location, current.boundsRange.length],
                        "textBounds": [current.textBounds.minX, current.textBounds.minY,
                                       current.textBounds.width, current.textBounds.height],
                        "point": [current.point.x, current.point.y]
                    ])
                }
            },
            clickPointer: {
                try checkEffect()
                guard let selected else {
                    throw DriverError("scalar text-point preparation has no selected point", code: .invalidRequest)
                }
                try scalar.validate(selected)
                let click = try NativePointerIntent(
                    unit: "pointer_sequence", kind: "click", parameters: ["button": "left", "count": 1],
                    foreground: false, element: true)
                let checkPoint: @MainActor @Sendable () throws -> Void = {
                    try checkEffect()
                    try scalar.validate(selected)
                }
                try await scalarPointer(selected).deliver(
                    click, from: selected.point, to: nil, viewport: selected.mapping.frame.size,
                    check: checkPoint, guardOperation: checkPoint, onDispatch: {
                        clickStarted = true
                        onPreparation("verified scalar text-point focus")
                    }, onPosted: {},
                    onCleanupUnknown: onCleanupUnknown, delivery: .windowDirected)
            },
            receives: {
                try Self.scalarReceiverReady(
                    receives: {
                        try NativeReceiverSnapshot.read(
                            application: application, window: window, receiverRelation: receiverRelation).receives(element)
                    }, contentUnchanged: {
                        guard let selected else { return nil }
                        return try scalar.contentUnchanged(since: selected)
                    })
            }, selectionWritable: { false },
            restoreSelection: { _ in
                throw DriverError("scalar replacement does not restore the old selection", code: .invalidRequest)
            })
        do {
            try await Self.prepare(intent: .replace, access: access, check: checkEffect)
        } catch is NativePreparationUnavailable where clickStarted {
            throw DriverError(
                "scalar pointer preparation did not confirm the original receiver; no replacement text sent",
                code: .inputUnavailable, effect: .possible)
        }
    }

    /// Content is inspected only after the selected click has produced fresh
    /// receiver evidence. Checking immediately after posting could accept the
    /// old value before the application has consumed the event.
    static func scalarReceiverReady(
        receives: @MainActor () throws -> Bool, contentUnchanged: @MainActor () throws -> Bool?
    ) throws -> Bool {
        guard try receives() else { return false }
        guard try contentUnchanged() == true else {
            throw DriverError(
                "scalar content changed or became unavailable during pointer preparation; no replacement text sent",
                code: .inputUnavailable, effect: .possible)
        }
        return true
    }

    static func prepare(
        intent: NativeKeyboardIntent.Kind, access: PreparationAccess, check: @MainActor () throws -> Void,
        wait: @MainActor () async throws -> Void = { try await Task.sleep(for: .milliseconds(25)) }
    ) async throws {
        guard [.focus, .insert, .replace, .paste].contains(intent) else {
            throw DriverError("pointer focus is preparation only", code: .invalidRequest)
        }
        try check()
        if try access.ready() { return }
        // Insertion and paste preserve the existing caret or selection. Focus
        // only establishes this exact receiver; whole replacement owns its
        // later full-selection recipe.
        let preservesSelection = intent == .insert || intent == .paste
        let range = preservesSelection ? access.selection() : nil
        try await access.preparePointer()
        try await access.clickPointer()
        for attempt in 0..<12 {
            try check()
            if try access.receives() {
                if let range,
                    access.selection().map({ $0.location == range.location && $0.length == range.length }) != true
                {
                    guard access.selectionWritable() else {
                        throw DriverError(
                            "pointer focus could not preserve the editor selection; no requested input sent",
                            code: .inputUnavailable)
                    }
                    guard try access.restoreSelection(range) == .success else {
                        throw DriverError(
                            "editor selection restoration failed; no requested input sent", code: .inputUnavailable)
                    }
                    for restoration in 0..<12 {
                        try check()
                        if access.selection().map({ $0.location == range.location && $0.length == range.length }) == true {
                            return
                        }
                        if restoration < 11 { try await wait() }
                    }
                    throw DriverError(
                        "editor selection restoration is unconfirmed; no requested input sent", code: .inputUnavailable)
                }
                return
            }
            if attempt < 11 { try await wait() }
        }
        if preservesSelection {
            let preserved = range.map { original in
                access.selection().map {
                    $0.location == original.location && $0.length == original.length
                } == true
            } == true
            try check()
            guard preserved else {
                // No receiver authorizes a restoration write. A later route
                // must not adopt a caret changed by this preparation click.
                throw DriverError(
                    "pointer preparation did not establish the selected editor or preserve its selection; no requested input sent",
                    code: .inputUnavailable)
            }
        }
        throw NativePreparationUnavailable(
            description: "pointer preparation did not establish the selected editor; no requested input sent")
    }
}
