// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices

/// A preparation-only click on one confirmed native editor hit. Python chooses
/// this route after AX preparation; this unit never sends requested text/keys.
@MainActor struct NativePointerFocusUnit {
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

    private var pointer: NativeElementPointerUnit {
        NativeElementPointerUnit(application: application, window: window, element: element)
    }

    func backgroundPreparationAvailable() throws -> Bool { try pointer.backgroundPreparationAvailable() }

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
                    click, check: checkEffect, onDispatch: { onPreparation("verified editor pointer focus") },
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
