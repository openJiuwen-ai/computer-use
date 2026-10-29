// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices

/// One preselected full-text replacement recipe. The original Element and
/// receiver are owned by the surrounding operation; no editor lookup occurs.
@MainActor struct NativeReplacementUnit {
    enum Selection: Equatable {
        case range, shortcut, markers, selectAll
        var usesWindowCommand: Bool { self == .shortcut || self == .selectAll }
    }
    let element: AXUIElement

    func contract() -> Selection? {
        guard AXAccess.read(element, "AXEditable").value as? Bool != false else { return nil }
        let editable =
            AXAccess.read(element, "AXEditable").value as? Bool == true
            || AXAccess.writable(element, kAXValueAttribute).value == true
            || AXAccess.writable(element, kAXSelectedTextAttribute).value == true
        guard editable else { return nil }
        let numericWritable = AXAccess.writable(element, kAXSelectedTextRangeAttribute).value == true
        // Declared readable selection is needed to verify Select All. A role,
        // focusable flag or key-post return code cannot stand in for this fact.
        var attributes: CFArray?
        guard AXUIElementCopyAttributeNames(element, &attributes) == .success, let names = attributes as? [String]
        else { return numericWritable ? .range : nil }
        if AXAccess.read(element, kAXRoleAttribute).value as? String == kAXTextAreaRole,
            names.contains(kAXSelectedTextRangeAttribute), names.contains(NativeTextMarkerSelection.selectedAttribute)
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
        if numericWritable { return .range }
        if names.contains(kAXSelectedTextRangeAttribute) { return .shortcut }
        // Editable document roots (for example designMode WebKit) may expose
        // marker ranges instead of numeric text ranges. Marker selection alone
        // is NOT editing evidence: read-only pages expose it too.
        if AXAccess.read(element, kAXRoleAttribute).value as? String == "AXWebArea",
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

    /// Returns true only for an already-empty target replaced with empty text.
    /// Selection mutation and content delivery share one dispatch barrier. Any
    /// uncertain selection result stops here; there is no second recipe.
    func deliver(
        text: String, selection: Selection, delivery: NativeKeyboardDelivery, applicationPID: pid_t,
        check: @escaping @MainActor () throws -> Void, validateReceiver: @escaping @MainActor () async throws -> Void,
        onDispatch: @escaping @MainActor () throws -> Void, onPosted: @escaping @MainActor () -> Void,
        onCleanupUnknown: @escaping () -> Void
    ) async throws -> Bool {
        func validateImmediate() throws {
            try check()
            guard contract() == selection else {
                throw DriverError("replacement editing/selection contract changed", code: .inputUnavailable)
            }
        }
        func validate() async throws {
            if delivery.isScopedHID {
                try validateImmediate()
                try await validateReceiver()
            } else {
                try await validateReceiver()
                try validateImmediate()
            }
        }
        func beforeSelectionMutation() throws {
            if delivery.isScopedHID { try RequestDiagnostics.checkCancellation() } else { try validateImmediate() }
        }
        try await validate()
        guard let before = snapshot(selection) else {
            throw DriverError("target text extent is unavailable; no replacement sent", code: .inputUnavailable)
        }
        if before.empty && text.isEmpty { return true }
        if selection == .selectAll || !before.full {
            switch (selection, before) {
            case (.range, .numeric(let numeric)):
                try await validate()
                try selectRange(
                    count: numeric.count, check: beforeSelectionMutation, onDispatch: onDispatch, onPosted: onPosted)
            case (.markers, .markers(let markers)):
                try await validate()
                try select(
                    markers.range, attribute: NativeTextMarkerSelection.selectedAttribute,
                    check: beforeSelectionMutation, onDispatch: onDispatch, onPosted: onPosted)
            case (.shortcut, .numeric), (.selectAll, .numeric):
                try await Input.preparedKey(
                    "super+a", delivery: delivery, validate: validate, onDispatch: onDispatch, onPosted: onPosted,
                    onCleanupUnknown: onCleanupUnknown)
            default: throw DriverError("replacement selection representation changed", code: .inputUnavailable)
            }
        }
        func selected() -> Bool {
            // Keep the original exact numeric/full-marker proof. Some providers
            // map markers to the surrounding document even for an independently
            // editable text area. Only a shortened rich-editor selection needs
            // the additional opaque-marker ownership proof.
            snapshot(selection)?.full == true || selection == .selectAll && keyboardSelectionIsScoped()
        }
        // Selection changes may be asynchronous. Observe only, without
        // repeating Select All or comparing normal formatted text values.
        for attempt in 0..<12 {
            try await validate()
            if selected() { break }
            if attempt < 11 { try await Task.sleep(for: .milliseconds(25)) }
        }
        var contentStarted = false
        func beforeContent() async throws {
            try await validate()
            if !contentStarted, !selected() {
                throw DriverError(
                    selection == .selectAll
                        ? """
                        Select All did not produce a stable selection inside the addressed editor; no \
                        replacement text sent
                        """ : "the target does not have a confirmed full-text selection; no replacement text sent",
                    code: .inputUnavailable)
            }
            // Selection/parent queries can call into application code. A valid
            // range is not authority to use a receiver sampled before them.
            try await validate()
        }
        func posted() {
            contentStarted = true
            onPosted()
        }
        if text.isEmpty {
            try await Input.preparedKey(
                "BackSpace", delivery: delivery, validate: beforeContent, onDispatch: onDispatch, onPosted: posted,
                onCleanupUnknown: onCleanupUnknown)
        } else {
            try await Input.preparedText(
                text, delivery: delivery, applicationPID: applicationPID, validate: beforeContent,
                onDispatch: onDispatch, onPosted: posted, onCleanupUnknown: onCleanupUnknown)
        }
        return false
    }
}
