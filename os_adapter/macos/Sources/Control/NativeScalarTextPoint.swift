// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices

/// QUERY-only geometry for the original scalar editor's first character.
/// A control frame does not distinguish its editable text from stepper buttons.
/// This local preparation recipe never substitutes the control's center, chooses
/// a different editor, establishes focus, or sends a pointer event.
@MainActor struct NativeScalarTextPoint {
    let application: NSRunningApplication
    let window: AXUIElement
    let element: AXUIElement

    struct Sample {
        let mapping: WindowGeometry.WindowMapping
        let textBounds: CGRect
        let visible: CGRect
        let point: CGPoint
        let boundsRange = CFRange(location: 0, length: 1)
        let contentEvidence: ContentEvidence?
    }

    enum ContentEvidence {
        case number(NSNumber)
        case value(String)
        case text(String, range: CFRange)

        /// This is only preparation-effect evidence, never target identity.
        func unchanged(from original: ContentEvidence) -> Bool? {
            switch (original, self) {
            case (.number(let old), .number(let current)): return old.compare(current) == .orderedSame
            case (.value(let old), .value(let current)): return old == current
            case (.text(let old, _), .text(let current, _)): return old == current
            case (.value(let old), .text(let current, _)), (.text(let old, _), .value(let current)):
                return old == current
            default: return nil
            }
        }
    }

    private struct Geometry {
        let mapping: WindowGeometry.WindowMapping
        let textBounds: CGRect
        let elementFrame: CGRect
        let clipped: CGRect
        let contentEvidence: ContentEvidence?
    }

    static func content(
        read: (String) throws -> AXRead<CFTypeRef>, text: (CFRange) throws -> AXRead<CFTypeRef>
    ) rethrows -> ContentEvidence? {
        if case .value(let raw) = try read(kAXValueAttribute) {
            if let value = raw as? String, !value.isEmpty { return .value(value) }
            if let value = raw as? NSNumber, CFGetTypeID(value) != CFBooleanGetTypeID(), value.doubleValue.isFinite {
                return .number(value)
            }
        }
        guard case .value(let raw) = try read(kAXNumberOfCharactersAttribute),
            let number = raw as? NSNumber, CFGetTypeID(number) != CFBooleanGetTypeID(),
            let count = Int(number.stringValue), count >= 0, count <= 65_536
        else { return nil }
        let range = CFRange(location: 0, length: count)
        guard case .value(let rawText) = try text(range), let value = rawText as? String,
            value.utf16.count == count,
            case .value(let currentRaw) = try read(kAXNumberOfCharactersAttribute),
            let current = currentRaw as? NSNumber, CFGetTypeID(current) != CFBooleanGetTypeID(),
            number.compare(current) == .orderedSame
        else { return nil }
        return .text(value, range: range)
    }

    /// The parameterized read is a separate port: missing ordinary text or
    /// scalar value-only evidence must not trigger a speculative bounds query.
    static func firstCharacterBounds(
        selection: NativeReplacementUnit.Selection?, before: NativeReplacementUnit.Snapshot?, role: String?,
        characterCount: AXRead<CFTypeRef>, rangeWritable: Bool,
        query: (CFRange) throws -> AXRead<CFTypeRef>
    ) rethrows -> CGRect? {
        guard let selection, let before,
            NativeReplacementUnit.usesScalarTextRecipe(
                selection: selection, role: role, characterCount: characterCount,
                rangeWritable: rangeWritable, before: before),
            case .numeric(let numeric) = before, numeric.count > 0
        else { return nil }
        guard case .value(let raw) = try query(CFRange(location: 0, length: 1)),
            CFGetTypeID(raw) == AXValueGetTypeID()
        else { return nil }
        var bounds = CGRect.zero
        guard AXValueGetValue(unsafeDowncast(raw, to: AXValue.self), .cgRect, &bounds), usable(bounds) else {
            return nil
        }
        return bounds
    }

    private static func usable(_ rect: CGRect) -> Bool {
        !rect.isNull && !rect.isInfinite && rect.size.width > 0 && rect.size.height > 0
            && [rect.origin.x, rect.origin.y, rect.width, rect.height, rect.maxX, rect.maxY].allSatisfy(\.isFinite)
    }

    private static func visible(
        mapping: WindowGeometry.WindowMapping, textBounds: CGRect, elementFrame: CGRect, clipped: CGRect
    ) -> CGRect? {
        guard mapping.pid > 0, mapping.id != 0,
            [mapping.frame, textBounds, elementFrame, clipped].allSatisfy(usable)
        else { return nil }
        let result = textBounds.intersection(elementFrame).intersection(clipped).intersection(mapping.frame)
        return usable(result) ? result : nil
    }

    static func select(
        mapping: WindowGeometry.WindowMapping, textBounds: CGRect, elementFrame: CGRect, clipped: CGRect,
        contentEvidence: ContentEvidence? = nil
    ) -> Sample? {
        guard let visible = visible(
            mapping: mapping, textBounds: textBounds, elementFrame: elementFrame, clipped: clipped)
        else { return nil }
        let point = CGPoint(x: visible.midX, y: visible.midY)
        guard point.x.isFinite, point.y.isFinite, visible.contains(point) else { return nil }
        return Sample(
            mapping: mapping, textBounds: textBounds, visible: visible, point: point, contentEvidence: contentEvidence)
    }

    /// Revalidation retains the original point. A new center is never an
    /// alternative target after a selected preparation gesture has begun.
    static func retains(
        _ expected: Sample, mapping: WindowGeometry.WindowMapping, textBounds: CGRect,
        elementFrame: CGRect, clipped: CGRect
    ) -> Bool {
        guard mapping.pid == expected.mapping.pid, mapping.id == expected.mapping.id,
            mapping.frame == expected.mapping.frame, expected.point.x.isFinite, expected.point.y.isFinite,
            let visible = visible(mapping: mapping, textBounds: textBounds, elementFrame: elementFrame, clipped: clipped)
        else { return false }
        return visible.contains(expected.point)
    }

    private func originalPath() throws -> Bool {
        func sameProcess(_ object: AXUIElement) -> Bool {
            var pid: pid_t = 0
            return AXUIElementGetPid(object, &pid) == .success && pid == application.processIdentifier
        }
        guard sameProcess(element), sameProcess(window) else { return false }
        let deadline = ProcessInfo.processInfo.systemUptime + 0.15
        return try NativeElementHitSelection.reachesWindow(
            element, window: window, same: { CFEqual($0, $1) },
            parent: { object in
                guard let parent = AXAccess.element(object, kAXParentAttribute), sameProcess(parent) else { return nil }
                return parent
            },
            isSurface: { object in
                guard let role = AXAccess.read(object, kAXRoleAttribute).value as? String else { return true }
                return [kAXWindowRole, kAXSheetRole, "AXPopover"].contains(role)
            },
            canContinue: {
                try RequestDiagnostics.checkCancellation()
                return ProcessInfo.processInfo.systemUptime < deadline
            })
    }

    private func parameter(_ name: String, range: CFRange) throws -> AXRead<CFTypeRef> {
        try RequestDiagnostics.checkCancellation()
        var requested = range
        guard let parameter = AXValueCreate(.cfRange, &requested) else { return .unsupported }
        var raw: CFTypeRef?
        let status = AXUIElementCopyParameterizedAttributeValue(element, name as CFString, parameter, &raw)
        try RequestDiagnostics.checkCancellation()
        return .result(status, raw)
    }

    private func contentSnapshot() throws -> ContentEvidence? {
        try Self.content(
            read: {
                try RequestDiagnostics.checkCancellation()
                return AXAccess.read(element, $0)
            }, text: { try parameter(kAXStringForRangeParameterizedAttribute, range: $0) })
    }

    private func geometry(includeContent: Bool) throws -> Geometry? {
        try RequestDiagnostics.checkCancellation()
        let unit = NativeReplacementUnit(element: element)
        guard unit.contract() == .shortcut, let before = unit.snapshot(.shortcut) else { return nil }
        guard try originalPath() else { return nil }
        let bounds = try Self.firstCharacterBounds(
            selection: .shortcut, before: before, role: AXAccess.read(element, kAXRoleAttribute).value as? String,
            characterCount: AXAccess.read(element, kAXNumberOfCharactersAttribute),
            rangeWritable: AXAccess.writable(element, kAXSelectedTextRangeAttribute).value == true,
            query: { try parameter(kAXBoundsForRangeParameterizedAttribute, range: $0) })
        guard let bounds else { return nil }
        let mapping = try NativeWindowUnit(application: application, window: window).mapping()
        let viewport = try NativeElementViewport.sample(element: element, window: window, mapping: mapping)
        let contentEvidence = try includeContent ? contentSnapshot() : nil
        guard !includeContent || contentEvidence != nil else { return nil }
        // Parameterized/viewport getters can call application code. Keep the
        // retained object and original-window path live after those queries.
        guard try originalPath() else { return nil }
        try RequestDiagnostics.checkCancellation()
        return Geometry(
            mapping: mapping, textBounds: bounds, elementFrame: viewport.frame, clipped: viewport.clipped,
            contentEvidence: contentEvidence)
    }

    func sample() throws -> Sample? {
        try Self.optionalSample {
            guard let current = try geometry(includeContent: true) else { return nil }
            return Self.select(
                mapping: current.mapping, textBounds: current.textBounds,
                elementFrame: current.elementFrame, clipped: current.clipped, contentEvidence: current.contentEvidence)
        }
    }

    /// Missing pointer geometry disqualifies only this optional candidate.
    /// Identity, cancellation and other failures retain their original meaning.
    static func optionalSample(_ query: () throws -> Sample?) rethrows -> Sample? {
        do { return try query() }
        catch let error as DriverError where error.code == .inputUnavailable { return nil }
    }

    func requireSample() throws -> Sample {
        guard let selected = try sample() else {
            throw DriverError("the original scalar editor has no confirmed visible text point", code: .inputUnavailable)
        }
        return selected
    }

    func validate(_ expected: Sample) throws {
        guard let current = try geometry(includeContent: false),
            Self.retains(
                expected, mapping: current.mapping, textBounds: current.textBounds,
                elementFrame: current.elementFrame, clipped: current.clipped)
        else {
            throw DriverError(
                "the original scalar editor text point changed; no new point was selected", code: .inputUnavailable)
        }
    }

    /// Invoke once after the selected preparation click. Selection changes are
    /// expected; a changed scalar value cannot authorize sending the body text.
    /// Missing evidence stays unknown rather than being promoted to unchanged.
    func contentUnchanged(since original: Sample) throws -> Bool? {
        try RequestDiagnostics.checkCancellation()
        guard let before = original.contentEvidence, try originalPath(), let current = try contentSnapshot(),
            try originalPath()
        else { return nil }
        return current.unchanged(from: before)
    }
}
