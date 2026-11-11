// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices

/// One preselected full-text replacement recipe. The original Element and
/// receiver are owned by the surrounding operation; no editor lookup occurs.
@MainActor struct NativeReplacementUnit {
    enum Selection: Equatable {
        case range, shortcut, structuredScalarShortcut, markers, selectAll
        case keyboardSelectAll(NativeKeyboardTextEntry)

        var keyboardEntry: NativeKeyboardTextEntry? {
            if case .keyboardSelectAll(let entry) = self { return entry }
            return nil
        }

        var axContract: Selection { self == .structuredScalarShortcut ? .shortcut : self }
        var usesWindowCommand: Bool { axContract == .shortcut || self == .selectAll || keyboardEntry != nil }
    }
    let element: AXUIElement

    func contract() -> Selection? {
        guard AXAccess.read(element, "AXEditable").value as? Bool != false else { return nil }
        let editable =
            AXAccess.read(element, "AXEditable").value as? Bool == true
            || AXAccess.writable(element, kAXValueAttribute).value == true
            || AXAccess.writable(element, kAXSelectedTextAttribute).value == true
        guard editable else { return nil }
        let role = AXAccess.read(element, kAXRoleAttribute).value as? String
        let numericSelection: Selection = role == kAXIncrementorRole ? .shortcut : .range
        let numericWritable = AXAccess.writable(element, kAXSelectedTextRangeAttribute).value == true
        // Declared readable selection is needed to verify Select All. A role,
        // focusable flag or key-post return code cannot stand in for this fact.
        var attributes: CFArray?
        guard AXUIElementCopyAttributeNames(element, &attributes) == .success, let names = attributes as? [String]
        else { return numericWritable ? numericSelection : nil }
        if role == kAXTextAreaRole, names.contains(kAXSelectedTextRangeAttribute),
            names.contains(NativeTextMarkerSelection.selectedAttribute)
        {
            var parameters: CFArray?
            if AXUIElementCopyParameterizedAttributeNames(element, &parameters) == .success,
                let names = parameters as? [String], names.contains("AXUIElementForTextMarker"),
                names.contains("AXTextMarkerRangeForUIElement")
            {
                // A marker-backed editor's flattened AX count need not be its
                // editable range. Choose this one recipe before any dispatch;
                // never try it after a numeric/marker setter may have run.
                return .selectAll
            }
        }
        // Scalar editors have an active segment/caret independent of the
        // reported AX extent. Their editing command establishes whole-input
        // replacement; ordinary text and object editing retain native ranges.
        if numericWritable { return numericSelection }
        if names.contains(kAXSelectedTextRangeAttribute) { return .shortcut }
        // Editable document roots (for example designMode WebKit) may expose
        // marker ranges instead of numeric text ranges. Marker selection alone
        // is NOT editing evidence: read-only pages expose it too.
        if role == "AXWebArea",
            [
                NativeTextMarkerSelection.selectedAttribute, NativeTextMarkerSelection.startAttribute,
                NativeTextMarkerSelection.endAttribute
            ].allSatisfy(names.contains),
            AXAccess.writable(element, NativeTextMarkerSelection.selectedAttribute).value == true
        {
            return .markers
        }
        return nil
    }

    func snapshot() -> NativeTextSelection? { NativeTextSelection.read { AXAccess.read(element, $0) } }

    enum Snapshot {
        case numeric(NativeTextSelection), markers(NativeTextMarkerSelection)
        var full: Bool {
            switch self {
            case .numeric(let value): value.full
            case .markers(let value): value.full
            }
        }
        var empty: Bool {
            switch self {
            // The count field is a scalar native character extent, not a collection.
            // swiftlint:disable:next empty_count
            case .numeric(let value): value.count == 0
            case .markers(let value): value.empty
            }
        }
    }

    func snapshot(_ selection: Selection) -> Snapshot? {
        if let entry = selection.keyboardEntry { return entry.selection(element).map(Snapshot.numeric) }
        if selection != .markers { return snapshot().map(Snapshot.numeric) }
        func parameter(_ name: String, _ value: CFTypeRef) -> AXRead<CFTypeRef> {
            var result: CFTypeRef?
            let status = AXUIElementCopyParameterizedAttributeValue(element, name as CFString, value, &result)
            return .result(status, result)
        }
        func whole() -> AXRead<CFTypeRef> {
            // These are document endpoints, hence the WebArea contract above.
            // RangeForUIElement can include a synthetic terminal paragraph
            // separator which cannot be selected in an empty editing document.
            guard let start = AXAccess.read(element, NativeTextMarkerSelection.startAttribute).value,
                let end = AXAccess.read(element, NativeTextMarkerSelection.endAttribute).value,
                CFGetTypeID(start) == AXTextMarkerGetTypeID(), CFGetTypeID(end) == AXTextMarkerGetTypeID()
            else { return .unsupported }
            return .value(
                AXTextMarkerRangeCreate(
                    nil, unsafeDowncast(start, to: AXTextMarker.self), unsafeDowncast(end, to: AXTextMarker.self)))
        }
        return NativeTextMarkerSelection.read(
            whole: whole, selected: { AXAccess.read(element, NativeTextMarkerSelection.selectedAttribute) },
            valid: { parameter("AXTextMarkerIsValid", $0).value as? Bool == true }
        ).map(Snapshot.markers)
    }

    /// A scalar ValueInterface alone is not a text editor. Require its own
    /// native character extent and one readable, writable numeric selection;
    /// a display string, toolkit identifier or role alone grants no recipe.
    static func usesScalarTextRecipe(
        selection: Selection, role: String?, characterCount: AXRead<CFTypeRef>, rangeWritable: Bool,
        before: Snapshot
    ) -> Bool {
        guard selection == .shortcut, role == kAXIncrementorRole, rangeWritable,
            case .numeric(let numeric) = before, numeric.contiguous,
            numeric.location != nil, numeric.length != nil,
            case .value(let raw) = characterCount, let number = raw as? NSNumber,
            CFGetTypeID(number) != CFBooleanGetTypeID(), let count = Int(number.stringValue), count >= 0
        else { return false }
        return count == numeric.count
    }

    func structuredScalarShortcut(_ selection: Selection) -> Selection {
        guard selection == .shortcut, let before = snapshot(selection), hasScalarTextRecipe(before) else {
            return selection
        }
        return .structuredScalarShortcut
    }

    private func hasScalarTextRecipe(_ before: Snapshot) -> Bool {
        guard AXAccess.read(element, kAXRoleAttribute).value as? String == kAXIncrementorRole else { return false }
        return Self.usesScalarTextRecipe(
            selection: .shortcut, role: kAXIncrementorRole,
            characterCount: AXAccess.read(element, kAXNumberOfCharactersAttribute),
            rangeWritable: AXAccess.writable(element, kAXSelectedTextRangeAttribute).value == true, before: before)
    }

    private func keyboardSelectionIsScoped() -> Bool {
        func owns(_ marker: AXTextMarker) -> Bool {
            var raw: CFTypeRef?
            guard
                AXUIElementCopyParameterizedAttributeValue(
                    element, "AXUIElementForTextMarker" as CFString, marker, &raw) == .success, let raw,
                CFGetTypeID(raw) == AXUIElementGetTypeID()
            else { return false }
            func known(_ value: AXRead<Bool>) -> Bool? {
                switch value {
                case .value(let value): return value
                case .unsupported, .unavailable(.noValue): return false
                default: return nil
                }
            }
            return NativeKeyboardSelection.belongs(unsafeDowncast(raw, to: AXUIElement.self), to: element) { current in
                let editable: Bool?
                switch AXAccess.read(current, "AXEditable") {
                case .value(let value): editable = value as? Bool
                case .unsupported, .unavailable(.noValue): editable = false
                default: editable = nil
                }
                let reportedEditor: AXUIElement?
                switch AXAccess.read(current, "AXEditableAncestor") {
                case .value(let raw):
                    guard CFGetTypeID(raw) == AXUIElementGetTypeID() else { return nil }
                    reportedEditor = unsafeDowncast(raw, to: AXUIElement.self)
                case .unsupported, .unavailable(.noValue): reportedEditor = nil
                default: return nil
                }
                let editing = [
                    editable, known(AXAccess.writable(current, kAXValueAttribute)),
                    known(AXAccess.writable(current, kAXSelectedTextAttribute))
                ]
                let capability: Bool? = editing.contains(true) ? true : editing.contains(nil) ? nil : false
                return .init(
                    role: AXAccess.read(current, kAXRoleAttribute).value as? String, editingCapability: capability,
                    parent: AXAccess.element(current, kAXParentAttribute), reportedEditor: reportedEditor)
            }
        }
        return NativeKeyboardSelection.isScoped(
            numeric: snapshot, selected: { AXAccess.read(element, NativeTextMarkerSelection.selectedAttribute) },
            owns: owns)
    }

    func selectRange(
        count: Int, check: @MainActor () throws -> Void, onDispatch: @MainActor () throws -> Void,
        onPosted: @MainActor () -> Void
    ) throws {
        var all = CFRange(location: 0, length: count)
        guard count >= 0, let value = AXValueCreate(.cfRange, &all) else {
            throw DriverError("cannot allocate replacement selection", code: .inputUnavailable)
        }
        try select(
            value, attribute: kAXSelectedTextRangeAttribute, check: check, onDispatch: onDispatch, onPosted: onPosted)
    }

    private func select(
        _ value: CFTypeRef, attribute: String, check: @MainActor () throws -> Void,
        onDispatch: @MainActor () throws -> Void, onPosted: @MainActor () -> Void
    ) throws {
        try check()
        try onDispatch()
        let status = AXMessaging.setAttributeValue(element, attribute as CFString, value)
        guard status == .success else {
            throw DriverError(
                "replacement selection returned AX error \(status.rawValue); effect unknown, not retried",
                code: .executionUnknown, effect: .possible)
        }
        onPosted()
    }

    /// Only an observed whole range that later became a caret can be restored.
    /// Query the unchanged literal before rechecking the range: querying Value
    /// can itself collapse the range in some native editor implementations.
    static func restoreCollapsedRange(
        original: [UInt16], fullObserved: Bool, readText: () -> [UInt16]?,
        readRange: () -> NativeTextSelection?, validateReceiver: () async throws -> Void,
        restore: (Int) throws -> Void
    ) async throws -> Bool {
        guard fullObserved, !original.isEmpty, let lost = readRange(),
            lost.count == original.count, lost.contiguous, lost.location != nil, lost.length == 0,
            readText() == original
        else { return false }
        try await validateReceiver()
        guard readText() == original, readRange() == lost else { return false }
        try restore(original.count)
        return true
    }

    /// Returns true only for an already-empty target replaced with empty text.
    /// Selection mutation and content delivery share one dispatch barrier. Any
    /// uncertain selection result stops here; there is no second recipe.
    func deliver(
        text: String, selection: Selection, delivery: NativeKeyboardDelivery, applicationPID: pid_t,
        check: @escaping @MainActor () throws -> Void,
        validateReceiver: @escaping @MainActor () async throws -> Void,
        onDispatch: @escaping @MainActor () throws -> Void, onPosted: @escaping @MainActor () -> Void,
        onCleanupUnknown: @escaping () -> Void, windowReceiver: NativeReplacementWindowReceiver? = nil
    ) async throws -> Bool {
        func validateImmediate() throws {
            try check()
            let valid = if let entry = selection.keyboardEntry {
                NativeKeyboardTextEntry.read(element) == entry
            } else { contract() == selection.axContract }
            guard valid else {
                throw DriverError("replacement editing/selection contract changed", code: .inputUnavailable)
            }
        }
        let validate: @MainActor () async throws -> Void = {
            try await validateReceiver()
            try validateImmediate()
        }
        let beforeSelectionMutation: @MainActor () throws -> Void = { try validateImmediate() }
        try await validate()
        guard let before = snapshot(selection) else {
            throw DriverError("target text extent is unavailable; no replacement sent", code: .inputUnavailable)
        }
        let requiresScalarProof = selection == .structuredScalarShortcut
        let scalarRecipe = selection.axContract == .shortcut && (!text.isEmpty || requiresScalarProof)
            && hasScalarTextRecipe(before)
        if requiresScalarProof && !scalarRecipe {
            throw NativeRouteUnavailable(description: "the original scalar replacement proof is unavailable")
        }
        if before.empty && text.isEmpty { return true }
        func boundedLiteral() -> [UInt16]? {
            switch AXAccess.read(element, kAXSubroleAttribute) {
            case .value(let raw):
                guard let subrole = raw as? String, subrole != kAXSecureTextFieldSubrole else { return nil }
            case .unsupported, .unavailable(.noValue): break
            default: return nil
            }
            guard let value = AXAccess.read(element, kAXValueAttribute).value as? String,
                (1...4096).contains(value.utf16.count) else { return nil }
            return Array(value.utf16)
        }
        let recoveryLiteral: [UInt16]?
        if selection == .range, case .numeric(let range) = before, !range.full, (1...4096).contains(range.count),
            let value = boundedLiteral(), value.count == range.count
        {
            recoveryLiteral = value
        } else { recoveryLiteral = nil }
        // Structured scalar editors can validate each intermediate character
        // and retain a segment independently of their full AX range. Establish
        // editing, a forward complete range, and one whole-string input. Qt
        // subclasses/objectNames need not expose a framework class in their
        // identifier, so the recipe uses this editor's text contract instead.
        var atomicText: Input.AtomicTextEvents?
        if !text.isEmpty, scalarRecipe {
            guard let events = try Input.prepareAtomicText(text, foreground: delivery.isGlobal) else {
                throw DriverError(
                    "the complete scalar replacement cannot be encoded; no replacement input sent",
                    code: .inputUnavailable)
            }
            atomicText = events
        }
        let textFragments =
            atomicText == nil && !text.isEmpty
            ? try Input.prepareTextFragments(text, foreground: delivery.isGlobal) : nil
        // An existing full AX range can have been set without the editor's
        // Select All command. Establish keyboard editing even in that case.
        if selection.usesWindowCommand || !before.full {
            switch (selection.axContract, before) {
            case (.range, .numeric(let numeric)):
                try await validate()
                try selectRange(
                    count: numeric.count, check: beforeSelectionMutation, onDispatch: onDispatch, onPosted: onPosted)
            case (.markers, .markers(let markers)):
                try await validate()
                try select(
                    markers.range, attribute: NativeTextMarkerSelection.selectedAttribute,
                    check: beforeSelectionMutation, onDispatch: onDispatch, onPosted: onPosted)
            case (.shortcut, .numeric), (.selectAll, .numeric), (.keyboardSelectAll, .numeric):
                try await Input.preparedKey(
                    "super+a", delivery: delivery, validate: validate, onDispatch: onDispatch, onPosted: onPosted,
                    onCleanupUnknown: onCleanupUnknown)
            default: throw DriverError("replacement selection representation changed", code: .inputUnavailable)
            }
        }
        if atomicText != nil {
            guard case .numeric(let numeric) = before else {
                throw DriverError("structured editor text extent changed", code: .inputUnavailable)
            }
            try await validate()
            try selectRange(
                count: numeric.count, check: beforeSelectionMutation, onDispatch: onDispatch, onPosted: onPosted)
        }
        var fullOriginalRangeObserved = false
        func selected() -> Bool {
            // Keep the original exact numeric/full-marker proof. Some providers
            // map markers to the surrounding document even for an independently
            // editable text area. Only a shortened rich-editor selection needs
            // the additional opaque-marker ownership proof.
            if let recoveryLiteral {
                let range = NativeTextSelection.readBeforeRanges { AXAccess.read(element, $0) }
                if range?.full == true && range?.count == recoveryLiteral.count {
                    fullOriginalRangeObserved = true
                }
                return range?.full == true
            }
            return snapshot(selection)?.full == true || selection == .selectAll && keyboardSelectionIsScoped()
        }
        func liveRecoveryRange(_ extent: Int) -> NativeTextSelection? {
            NativeTextSelection.readRanges(textUTF16Count: extent) { AXAccess.read(element, $0) }
        }
        // Both initial verification and possible recovery consume the same
        // observation allowance and remain inside the original operation.
        var observationsRemaining = 12
        func observeFullSelection() async throws -> Bool {
            while observationsRemaining > 0 {
                observationsRemaining -= 1
                try await validate()
                if selected() { return true }
                if observationsRemaining > 0 { try await Task.sleep(for: .milliseconds(25)) }
            }
            return false
        }
        _ = try await observeFullSelection()
        var rangeRecoveryAttempted = false
        var receiverRecoveryAttempted = false
        var contentStarted = false
        func validateBeforeContent() async throws -> Bool {
            do {
                try await validate()
                return false
            } catch let failure as DriverError where failure.code == .inputUnavailable {
                guard let windowReceiver, let recoveryLiteral, fullOriginalRangeObserved,
                    !receiverRecoveryAttempted, !rangeRecoveryAttempted, observationsRemaining > 1,
                    let deadline = RequestDiagnostics.deadline, deadline - ProcessInfo.processInfo.systemUptime >= 0.025
                else { throw failure }
                try validateImmediate()
                guard try windowReceiver.prepare(ifUnchanged: { boundedLiteral() == recoveryLiteral }) else {
                    throw failure
                }
                receiverRecoveryAttempted = true
                // Share the selected recipe's allowance. A different receiver
                // or desktop event ends recovery immediately; only the proved
                // Window-only transition can still settle to the original.
                while observationsRemaining > 0 {
                    observationsRemaining -= 1
                    do {
                        try await validate()
                        return true
                    } catch let pending as DriverError where pending.code == .inputUnavailable {
                        guard observationsRemaining > 0, try windowReceiver.windowOnly() else { throw pending }
                    }
                    try await Task.sleep(for: .milliseconds(25))
                }
                throw failure
            }
        }
        func requireFullSelection() async throws {
            if selected() { return }
            if let recoveryLiteral, !rangeRecoveryAttempted, observationsRemaining > 0 {
                rangeRecoveryAttempted = true
                if try await Self.restoreCollapsedRange(
                    original: recoveryLiteral, fullObserved: fullOriginalRangeObserved, readText: boundedLiteral,
                    readRange: { liveRecoveryRange(recoveryLiteral.count) },
                    validateReceiver: validate,
                    restore: { count in
                        try selectRange(
                            count: count, check: beforeSelectionMutation,
                            onDispatch: onDispatch, onPosted: onPosted)
                    }), try await observeFullSelection()
                {
                    return
                }
            }
            throw DriverError(
                selection == .selectAll
                    ? "Select All did not produce a stable selection inside the addressed editor; no replacement text sent"
                    : "the target does not have a confirmed full-text selection; no replacement text sent",
                code: .inputUnavailable)
        }
        let beforeContent: @MainActor () async throws -> Void = {
            if contentStarted {
                try await validate()
                return
            }
            _ = try await validateBeforeContent()
            try await requireFullSelection()
            // Selection/parent queries can call into application code. A valid
            // range is not authority to use a receiver sampled before them.
            if try await validateBeforeContent() {
                try await requireFullSelection()
                try await validate()
            }
            if (rangeRecoveryAttempted || receiverRecoveryAttempted), let recoveryLiteral {
                guard boundedLiteral() == recoveryLiteral, liveRecoveryRange(recoveryLiteral.count)?.full == true else {
                    throw DriverError(
                        "the restored selection changed before replacement dispatch", code: .inputUnavailable)
                }
                if receiverRecoveryAttempted {
                    try await validate()
                    guard windowReceiver?.desktopUnchanged() == true else {
                        throw DriverError("desktop input changed during receiver recovery", code: .inputUnavailable)
                    }
                }
            }
        }
        let posted: @MainActor () -> Void = {
            contentStarted = true
            onPosted()
        }
        if text.isEmpty {
            try await Input.preparedKey(
                "BackSpace", delivery: delivery, validate: beforeContent, onDispatch: onDispatch, onPosted: posted,
                onCleanupUnknown: onCleanupUnknown)
        } else if let atomicText {
            try await Input.preparedAtomicText(
                atomicText, delivery: delivery, applicationPID: applicationPID, validate: beforeContent,
                onDispatch: onDispatch, onPosted: posted, onCleanupUnknown: onCleanupUnknown)
        } else if let textFragments {
            for fragment in textFragments {
                try await Input.preparedAtomicText(
                    fragment, delivery: delivery, applicationPID: applicationPID, validate: beforeContent,
                    onDispatch: onDispatch, onPosted: posted, onCleanupUnknown: onCleanupUnknown)
            }
        } else {
            try await Input.preparedText(
                text, delivery: delivery, applicationPID: applicationPID, validate: beforeContent,
                onDispatch: onDispatch, onPosted: posted, onCleanupUnknown: onCleanupUnknown)
        }
        return false
    }
}
