// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices

/// One session's native objects, raw facts, window catalogue and addressed units.
/// This facade does not find editors, project observations or choose input routes.
@MainActor final class NativeApplicationAccess: NativeQueryObjectAccess, NativeControlObjectAccess {
    typealias ReadRoot = NativeApplicationReader.ReadRoot
    typealias Window = NativeApplicationReader.Window
    private let registry: ApplicationElements
    private let reader: NativeApplicationReader
    private let control: NativeElementControl
    private let facts: NativeFactCollector
    init(windowQuery: NativeWindowQueryAPI? = nil) {
        let registry = ApplicationElements()
        let inspection = NativeElementInspection(registry: registry)
        self.registry = registry
        self.reader = NativeApplicationReader(registry: registry, inspection: inspection, query: windowQuery)
        self.control = NativeElementControl(registry: registry, inspection: inspection)
        self.facts = NativeFactCollector(registry: registry, inspection: inspection)
    }

    func retainNode(_ ref: String) -> (() -> Void)? { registry.retain(ref) }

    /// Consume a directory's positive end-of-lifetime evidence, never a missing
    /// node in a partial content observation or a cache capacity decision.
    func retireScope(owner: NSRunningApplication, scope: String) {
        registry.invalidate { $0.application.isEqual(owner) && $0.scope == scope }
    }

    /// Decoration reads retained identities only. Geometry is resolved on the
    /// pointer's separate queue, never by adding AX work to the input path.
    func visualElement(_ ref: String) -> AXUIElement? { registry.target(ref)?.element }

    /// A locator borrows the exact registered object, not an input-ready editor.
    /// Geometry availability is checked by the caller; action, focus, role and
    /// ancestor readiness cannot prevent resolving a point for human input.
    func pointElement(_ ref: String, scope: String?) throws -> AXUIElement {
        guard let target = registry.target(ref), target.scope == scope,
            !target.application.isTerminated, target.application.launchDate == target.launched
        else { throw DriverError("original point target expired", code: .staleTarget) }
        return target.element
    }

    func observationScope(_ app: NSRunningApplication, window: AXUIElement?, reference: String? = nil) -> String {
        return registry.observationScope(app, window: window, reference: reference)
    }

    func windowReference(_ window: NativeWindows.Window, element: AXUIElement? = nil) -> String {
        return registry.windowReference(window, element: element)
    }

    func retireWindowReference(_ window: NativeWindows.Window) {
        registry.retireWindowReference(window)
    }

    func nativeScope(_ window: NativeWindows.Window, owner: NSRunningApplication) -> String {
        return registry.nativeScope(window, owner: owner)
    }

    func readRoot(_ ref: String) throws -> ReadRoot { return try reader.readRoot(ref) }

    func nativeFacts(
        _ ref: String, session: String, fields: [String], maxNodes: Int, maxDepth: Int,
        childrenPage: NativeInspectQuery.ChildrenPage? = nil, relationPage: NativeInspectQuery.RelationPage? = nil,
        advertisedFields: [String] = [], followRelations: [String] = [], textContent: Bool = false,
        actionDetail: NativeActionDetail = .full, focusedElement: Bool = false
    ) throws -> [String: Any] {
        try facts.collect(
            ref, session: session, fields: fields, maxNodes: maxNodes, maxDepth: maxDepth, childrenPage: childrenPage,
            relationPage: relationPage, advertisedFields: advertisedFields, followRelations: followRelations,
            textContent: textContent, actionDetail: actionDetail, focusedElement: focusedElement)
    }

    func nativeReadTarget(_ ref: String) throws -> ApplicationElements.Target { try facts.readTarget(ref) }

    func nativeRelation(_ ref: String, session: String, property: String, cursor: String?, limit: Int) throws
        -> [String: Any]
    { try facts.relation(ref, session: session, property: property, cursor: cursor, limit: limit) }

    func registerNativeRoot(_ element: AXUIElement, app: NSRunningApplication, scope: String) throws -> String {
        try facts.registerRoot(element, app: app, scope: scope)
    }

    /// Reuse exact content already acquired in its host scope. This lookup
    /// neither samples AX again nor changes an existing element's binding.
    func observedNativeNode(_ element: AXUIElement, app: NSRunningApplication, scope: String) -> String? {
        registry.observed(.init(
            pid: app.processIdentifier, launched: app.launchDate, element: element, scope: scope))?.id
    }

    func readText(_ ref: String, offset: Int, length: Int) throws -> [String: Any] {
        return try reader.readText(ref, offset: offset, length: length)
    }

    func windows(
        _ app: NSRunningApplication, nativeWindows: [NativeWindows.Window] = [], contentRoot: AXUIElement? = nil,
        purpose: NativeWindowQueryPurpose = .observation, profile: NativeInspectQuery.SurfaceProfile = .accessibility,
        modalHints: [NativeApplicationReader.ModalHint] = [],
        reportIssue: ((NativeApplicationReader.WindowIssue) -> Void)? = nil
    ) throws -> [Window] {
        return try reader.windows(
            app, nativeWindows: nativeWindows, contentRoot: contentRoot, purpose: purpose, profile: profile,
            modalHints: modalHints, reportIssue: reportIssue)
    }

    func inputBoundary(
        _ app: NSRunningApplication, nativeWindows: [NativeWindows.Window], contentRoot: AXUIElement?,
        required: [AXUIElement], modalHints: [NativeApplicationReader.ModalHint] = [],
        expandMissingRequired: Bool = true
    ) throws -> NativeApplicationReader.InputBoundary {
        try reader.inputBoundary(app, nativeWindows: nativeWindows, contentRoot: contentRoot, required: required,
                                 modalHints: modalHints, expandMissingRequired: expandMissingRequired)
    }

    func knownWindowReference(_ element: AXUIElement, app: NSRunningApplication, process: NativeProcessIdentity)
        -> String?
    {
        registry.knownReference(
            for: .init(pid: process.pid, launched: app.launchDate, element: element, incarnation: process.incarnation))
    }

    func windowParents(_ windows: [Window], native: [NativeWindows.Window], pid: pid_t) -> NativeWindowParents.Result {
        reader.windowParents(windows, native: native, pid: pid)
    }

    /// Read the current native focus against this exact catalogue. Titles,
    /// z-order and the app's main window cannot substitute for AXFocusedWindow.
    func focusedWindow(_ app: NSRunningApplication, windows: [Window]) -> String? {
        reader.focusedWindow(app, windows: windows)
    }

    func nativeActionFacts(_ ref: String) throws -> NativeElementControl.ActionFacts {
        try control.nativeActionFacts(ref)
    }

    func nativeExplicitActionFacts(_ ref: String, request: String) throws -> NativeElementControl.ActionFacts {
        try control.nativeActionFacts(ref, explicitRequest: request)
    }

    func nativeSelectionContract(_ ref: String) throws -> NativeRowSelection.Contract? {
        try control.nativeSelectionContract(ref)
    }

    func executeNativeSelection(
        _ ref: String, contract: NativeRowSelection.Contract, onDispatch: @MainActor () throws -> Void,
        onReturned: @MainActor () -> Void
    ) throws -> NativeElementControl.ActionResult {
        try control.executeNativeSelection(ref, contract: contract, onDispatch: onDispatch, onReturned: onReturned)
    }

    func executeNativeAction(
        _ ref: String, action: String, description: String?, onDispatch: @MainActor () throws -> Void,
        onReturned: @MainActor () -> Void = {}
    ) throws {
        try control.executeNativeAction(
            ref, action: action, description: description, onDispatch: onDispatch, onReturned: onReturned)
    }

    func executeNativeExplicitAction(
        _ ref: String, request: String, action: String, onDispatch: @MainActor () throws -> Void,
        onReturned: @MainActor () -> Void
    ) throws {
        try control.executeNativeAction(
            ref, action: action, description: nil, explicitRequest: request,
            onDispatch: onDispatch, onReturned: onReturned)
    }

    func nativeTextValueFacts(
        _ ref: String, replacementText: String, originalWindow: () throws -> AXUIElement?
    ) throws -> NativeElementControl.TextValueFacts {
        try control.nativeTextValueFacts(ref, replacementText: replacementText, originalWindow: originalWindow)
    }

    func executeNativeTextValue(
        _ ref: String, contract: NativeElementControl.TextValueContract, text: String,
        onDispatch: @MainActor () throws -> Void, originalWindow: () throws -> AXUIElement?
    ) throws -> NativeElementControl.ActionResult {
        try control.executeNativeTextValue(
            ref, contract: contract, text: text, onDispatch: onDispatch, originalWindow: originalWindow)
    }

}
