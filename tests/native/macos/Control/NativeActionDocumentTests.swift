// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices

@main enum NativeActionDocumentTests {
    @MainActor static func main() async throws {
        applicationMenuScopeIsDistinctFromWindowContent()
        menuCommandSourceSelectsAddressedPointer()
        attachedDialogReceiverNeedsCurrentParentProof()
        foregroundMenuKeepsItsConfirmedDocument()
        try retainedMenuMembershipUsesTheCompleteProviderRelation()
        await trackedMenuWaitsForActualPopupTransitions()
        try trackedMenuRequiresThePhysicalPointerInItsOriginalLeaf()
        try fixedDocumentEvidenceIsLazy()
        try fixedWindowQuerySeparatesAXAbsenceFromEndedLifetime()
        for explicit in [false, true] {
            for mode in [
                "selectable", "background", "initialTarget", "restoreOther", "fallback", "unavailable", "switched",
                "unknownAfter", "lateDocument", "modal", "target", "within", "action", "cancel", "uncertainSelection",
                "unconfirmedAction", "windowless", "unknownOnly", "custom"
            ] {
                let probe = DocumentProbe(mode: mode)
                let succeeded = try await probe.run(explicit: explicit)
                let blocked = [
                    "unavailable", "switched", "unknownAfter", "lateDocument", "modal", "target", "within", "action",
                    "cancel", "uncertainSelection"
                ].contains(mode)
                precondition(probe.presses == (blocked ? 0 : 1), "wrong action count: \(explicit) \(mode)")
                precondition(probe.document.b == 0, "another document received the action: \(mode)")
                precondition(probe.document.a == (blocked ? 0 : 1), "original document effect: \(mode)")
                precondition(succeeded == (!blocked && mode != "unconfirmedAction"), "wrong result: \(mode)")
                precondition(probe.document.foregrounds == (mode == "fallback" ? 1 : 0))
                if mode == "background" {
                    precondition(probe.trace.first == "local", "local activity must precede document selection")
                }
                if ["windowless", "unknownOnly", "initialTarget", "restoreOther", "custom"].contains(mode) {
                    precondition(probe.document.selections == 0, "no unnecessary document preparation")
                }
                if mode == "restoreOther" {
                    precondition(!probe.localPrepared && probe.localPreparationCalls == 0,
                                 "do not inject activity that can replace an already-correct original document")
                }
                if mode == "uncertainSelection" { precondition(probe.uncertain, "selection uncertainty was lost") }
            }
        }
        try await unauthorizedForeground()
        try retainedRequirement()
        try declarationRefreshes()
        try selectionResult()
        try await stagedEnabledPreparation()
        try await skipsPreparedDocument()
        try await documentPreparationBoundary()
        print("PASS original-window action receiver preparation, retained evidence and no-replay boundaries")
    }

    @MainActor static func applicationMenuScopeIsDistinctFromWindowContent() {
        let registry = ApplicationElements()
        let app = NSRunningApplication.current
        let window = AXUIElementCreateApplication(1_000_009)
        let local = registry.observationScope(app, window: window)
        precondition(!registry.isApplicationMenuScope(local, owner: app))
        let menu = registry.observationScope(app, window: nil)
        precondition(registry.isApplicationMenuScope(menu, owner: app))
        precondition(!registry.isApplicationMenuScope(local, owner: app))
    }

    static func menuCommandSourceSelectsAddressedPointer() {
        func requires(
            framework: Bool = true, role: String = kAXMenuItemRole, action: String = kAXPressAction,
            submenu: Bool? = false, shortcut: String? = "n"
        ) -> Bool {
            NativeMenuCommandPolicy.requiresPointer(
                electronFramework: framework, action: action, role: role, hasSubmenu: submenu,
                commandCharacter: shortcut)
        }
        precondition(requires())
        precondition(!requires(framework: false) && !requires(role: kAXMenuBarItemRole))
        precondition(!requires(action: "AXPick") && !requires(submenu: true) && !requires(submenu: nil))
        precondition(!requires(shortcut: nil) && !requires(shortcut: ""))
        let ready = NativeMenuCommandPolicy.offer(ready: true, available: true, canPrepare: false)
        let prepare = NativeMenuCommandPolicy.offer(ready: false, available: true, canPrepare: true)
        let missing = NativeMenuCommandPolicy.offer(ready: false, available: false, canPrepare: true)
        precondition(ready.delivery == .windowDirected && ready.admission == .ready)
        precondition(prepare.delivery == .windowDirected && prepare.admission == .preparationRequired)
        precondition(prepare.preparationOptions == ["foreground"] && prepare.backgroundUnavailable)
        precondition(missing.admission == .unavailable && missing.preparationOptions.isEmpty)
    }

    @MainActor static func attachedDialogReceiverNeedsCurrentParentProof() {
        let root = AXUIElementCreateApplication(1_000_018)
        let dialog = AXUIElementCreateApplication(1_000_019)
        func context(_ responder: AXRead<CFTypeRef>, focus: AXUIElement? = nil) -> NativeMenuContext {
            NativeMenuContext(application: .current, read: { _, key in
                switch key {
                case kAXMainWindowAttribute: .value(root)
                case kAXFocusedWindowAttribute: .value(focus ?? root)
                case kAXWindowsAttribute: .value([root, dialog] as CFArray)
                case kAXFocusedUIElementAttribute: responder
                default: .unsupported
                }
            })
        }
        precondition(context(.unavailable(.noValue)).attachedDialogWindows(root)?.count == 2)
        precondition(context(.unavailable(.cannotComplete)).attachedDialogWindows(root) == nil)
        precondition(context(.value(kCFBooleanTrue)).attachedDialogWindows(root) == nil)
        precondition(context(.unavailable(.noValue), focus: dialog).attachedDialogWindows(root) == nil)
        func owns(_ ax: [CGWindowID: CGWindowID], _ native: [CGWindowID: CGWindowID]) -> Bool {
            NativeMenuAttachedDialogs.provesAttachment(ids: [10, 20, 30], axParents: ax, nativeParents: native)
        }
        precondition(owns([30: 20], [20: 10, 30: 0]))
        precondition(owns([:], [20: 10, 30: 20]))
        precondition(!owns([:], [20: 10]))
        precondition(!owns([30: 10], [20: 10, 30: 20]))
        precondition(!owns([20: 30, 30: 20], [:]))
        precondition(!owns([20: 10], [30: 40]))
    }

    @MainActor static func foregroundMenuKeepsItsConfirmedDocument() {
        let target = AXUIElementCreateApplication(1_000_020)
        let sibling = AXUIElementCreateApplication(1_000_021)
        func receiver(_ focused: AXUIElement?, windows: [AXUIElement]) -> NativeMenuContext {
            NativeMenuContext(application: .current, read: { _, key in
                switch key {
                case kAXMainWindowAttribute: .value(target)
                case kAXFocusedWindowAttribute: focused.map { .value($0) } ?? .unavailable(.noValue)
                case kAXWindowsAttribute: .value(windows as CFArray)
                default: .unavailable(.noValue)
                }
            })
        }
        let selected = receiver(target, windows: [target])
        precondition(selected.confirms(target))
        // A menu can already have an exact foreground recipient while an
        // unavailable sheet relationship blocks rebuilding its document graph.
        precondition(!NativeMenuUnit.needsDocumentPreparation(foreground: true) { selected.confirms(target) })
        for competing in [receiver(sibling, windows: [target, sibling]), receiver(nil, windows: [target, sibling])] {
            precondition(NativeMenuUnit.needsDocumentPreparation(foreground: true) { competing.confirms(target) })
        }
        precondition(NativeMenuUnit.needsDocumentPreparation(foreground: false) { selected.confirms(target) })
    }

    @MainActor static func fixedDocumentEvidenceIsLazy() throws {
        let refreshedWindow = AXUIElementCreateApplication(1_000_010)
        var queries = 0
        let resolve = {
            queries += 1
            return NativeFixedInputWindow(application: .current, window: refreshedWindow)
        }
        func facts(_ action: String = kAXPressAction, menu: Bool = false) -> NativeElementControl.ActionFacts {
            .init(role: kAXButtonRole, actions: [.init(name: action, description: nil)], enabled: true,
                applicationMenuContext: menu, foreground: false)
        }
        let original = facts()
        let resolved = try original.resolvingDocument(resolve, subrole: { _ in kAXStandardWindowSubrole })
        guard let document = resolved.document as? NativeDocumentContext else {
            preconditionFailure("ordinary descendant lost its fixed document context")
        }
        precondition(CFEqual(document.window, refreshedWindow) && queries == 1)
        _ = try resolved.resolvingDocument(resolve)
        var logicalSurface = original
        logicalSurface.documentEvidence = .other
        _ = try logicalSurface.resolvingDocument(resolve)
        // A temporarily unavailable nested-surface subrole is not permission
        // to reinterpret its action as a parent document's responder action.
        logicalSurface.documentEvidence = .unknown
        logicalSurface.hasObservedDocumentSurface = true
        _ = try logicalSurface.resolvingDocument(resolve)
        _ = try facts(kAXShowMenuAction).resolvingDocument(resolve)
        _ = try facts(menu: true).resolvingDocument(resolve)
        precondition(queries == 1, "existing logical surfaces and independent actions must not query a document")
        for code: FailureCode in [.inputUnavailable, .observationIncomplete] {
            let unknown = try original.resolvingDocument { throw DriverError("optional query unavailable", code: code) }
            precondition(unknown.document == nil && unknown.actions?.count == 1,
                         "optional document evidence must not remove independent action candidates")
        }
        do {
            _ = try original.resolvingDocument { throw DriverError("original window ended", code: .staleTarget) }
            preconditionFailure("known identity conflicts must propagate")
        } catch let error as DriverError { precondition(error.code == .staleTarget) }
        struct Cancelled: Error {}
        do {
            _ = try original.resolvingDocument { throw Cancelled() }
            preconditionFailure("cancellation must propagate")
        } catch is Cancelled { }
    }

    @MainActor static func retainedMenuMembershipUsesTheCompleteProviderRelation() throws {
        let menuBar = AXUIElementCreateApplication(1_000_061)
        let menuItem = AXUIElementCreateApplication(1_000_062)
        let replacement = AXUIElementCreateApplication(1_000_063)
        let menu = AXUIElementCreateApplication(1_000_064)
        let leaf = AXUIElementCreateApplication(1_000_065)
        let path = [menuBar, menuItem, menu, leaf]
        var currentLeaf = leaf, listAvailable = true
        var api = AXRelationReader.API()
        api.count = { _, _ in (.attributeUnsupported, 0) }
        api.range = { _, _, _ in preconditionFailure("provider supports only the complete array getter") }
        api.single = { parent, _ in
            if CFEqual(parent, menuBar) { return (.success, [menuItem] as CFArray) }
            if CFEqual(parent, menuItem) { return (.success, [menu] as CFArray) }
            if CFEqual(parent, menu), listAvailable { return (.success, [currentLeaf] as CFArray) }
            return (.cannotComplete, nil)
        }
        let reader = AXRelationReader(api: api)
        let confirmed = try NativeMenuUnit.membership(path, root: menuBar, reader: reader)
        precondition(confirmed == .confirmed)
        currentLeaf = replacement
        let changed = try NativeMenuUnit.membership(path, root: menuBar, reader: reader)
        precondition(changed == .changed)
        listAvailable = false
        let unavailable = try NativeMenuUnit.membership(path, root: menuBar, reader: reader)
        precondition(unavailable == .unavailable)
    }

    @MainActor static func trackedMenuWaitsForActualPopupTransitions() async {
        var elapsed: TimeInterval = 0
        let popup = await NativeMenuUnit.waitForEvidence(seconds: 0.25, sample: {
            elapsed >= 0.061 ? CGWindowID(42) : nil
        }, now: { elapsed }, pause: { elapsed += $0 })
        precondition(popup == 42 && elapsed >= 0.061 && elapsed <= 0.076)

        elapsed = 0
        let closed = await NativeMenuUnit.waitForEvidence(seconds: 0.30, sample: {
            elapsed >= 0.204 ? true : nil
        }, now: { elapsed }, pause: { elapsed += $0 })
        precondition(closed == true && elapsed >= 0.204 && elapsed <= 0.219)

        elapsed = 0
        let missing = await NativeMenuUnit.waitForEvidence(seconds: 0.25, sample: { nil as CGWindowID? },
                                                          now: { elapsed }, pause: { elapsed += $0 })
        precondition(missing == nil && elapsed == 0.25)
    }

    @MainActor static func trackedMenuRequiresThePhysicalPointerInItsOriginalLeaf() throws {
        let leaf = CGRect(x: 94, y: 39, width: 276, height: 24)
        try NativeMenuUnit.requireTrackingPointer(CGPoint(x: 179, y: 49), in: leaf)
        for pointer in [CGPoint(x: 179, y: 72), CGPoint(x: 739, y: 990), nil] {
            do {
                try NativeMenuUnit.requireTrackingPointer(pointer, in: leaf)
                preconditionFailure("unproved physical menu receiver was admitted")
            } catch let error as DriverError {
                precondition(error.code == .inputUnavailable)
            }
        }
    }

    @MainActor static func fixedWindowQuerySeparatesAXAbsenceFromEndedLifetime() throws {
        let app = NSRunningApplication.current
        let root = AXUIElementCreateApplication(1_000_021)
        let refreshed = AXUIElementCreateApplication(1_000_022)
        let process = NativeProcessIdentity(
            pid: app.processIdentifier, parent: 0, seconds: 1, microseconds: 0, executable: "/fixture")
        var currentProcess: NativeProcessIdentity? = process
        var presence = WindowServerInfo.Presence.present([
            kCGWindowNumber as String: NSNumber(value: 70),
            kCGWindowOwnerPID as String: NSNumber(value: app.processIdentifier)])
        var declared: [AXUIElement]?
        var advertisedID: CGWindowID? = 70
        var catalogueReads = 0
        func value(_ node: AXUIElement, _ key: String) -> CFTypeRef? {
            if CFEqual(node, root) {
                if key == kAXWindowsAttribute { catalogueReads += 1; return declared.map { $0 as CFArray } }
                if key == kAXRoleAttribute { return kAXApplicationRole as CFString }
                return nil
            }
            guard CFEqual(node, refreshed) else { return nil }
            switch key {
            case kAXRoleAttribute: return kAXWindowRole as CFString
            case kAXSubroleAttribute: return kAXStandardWindowSubrole as CFString
            case kAXParentAttribute: return root
            case kAXChildrenAttribute, "AXSheets": return [] as CFArray
            case kAXModalAttribute: return kCFBooleanFalse
            default: return nil
            }
        }
        var query = NativeWindowQueryAPI(attribute: value)
        query.trusted = { true }; query.application = { _ in root }
        query.read = { node, key in value(node, key).map(AXRead.value) ?? .unavailable(.cannotComplete) }
        query.element = { node, key in key == kAXParentAttribute && CFEqual(node, refreshed) ? root : nil }
        query.pid = { _ in (.success, app.processIdentifier) }
        query.inputWindow = { _, _, _ in nil }; query.presentedWindow = { node, _, _ in node }
        query.nativeID = { CFEqual($0, refreshed) ? advertisedID : nil }
        query.hint = { node, _ in CFEqual(node, refreshed)
            ? .init(frame: CGRect(x: 0, y: 0, width: 200, height: 100), title: "", id: advertisedID) : nil }
        query.relations.count = { _, _ in (.success, 0) }
        query.relations.range = { _, _, _ in (.success, [] as CFArray) }
        query.relations.single = { _, _ in (.success, [] as CFArray) }
        let state = NativeDesktopState(
            applications: NativeApplicationAccess(windowQuery: query), appSelections: AppSelections(validateOwner: { _ in }))
        var services = NativeDirectory.WindowServices()
        services.process = { _ in currentProcess }; services.presence = { _ in presence }
        services.isGone = { _ in false }
        let directory = NativeDirectory(state: state, session: "fixed-window", windowServices: services)
        let native = NativeWindows.Window(
            application: app, process: process,
            mapping: .init(id: 70, pid: app.processIdentifier, frame: .zero, title: nil, width: 200, height: 100),
            onScreen: true)
        var binding = directory.retainDesktopWindow(native, owner: app)
        binding["session"] = "fixed-window"; binding["rootWindow"] = binding["window"]
        binding.removeValue(forKey: "window")
        // Only target identity fields belong to NativeTarget's closed schema.
        binding = binding.filter { ["session", "instance", "rootWindow", "surface", "node"].contains($0.key) }
        binding["capture"] = NSNull()
        let execution = NativeExecution(state: state, session: "fixed-window", cancelActive: {})
        execution.directory = directory
        let unavailableAX = try execution.scopes.fixedInputWindow(binding)
        precondition(unavailableAX == nil,
                     "unavailable AX catalogue must not report the live fixed window closed")
        let sampled = catalogueReads
        presence = .unavailable
        let unavailableNative = try execution.scopes.fixedInputWindow(binding)
        precondition(unavailableNative == nil && catalogueReads == sampled)
        presence = .present([
            kCGWindowNumber as String: NSNumber(value: 70),
            kCGWindowOwnerPID as String: NSNumber(value: app.processIdentifier)])
        declared = [refreshed]
        let fixed = try execution.scopes.fixedInputWindow(binding)
        precondition(fixed.map { CFEqual($0.window, refreshed) } == true,
                     "same native ID must resolve the current AX proxy")
        for endedByProcess in [false, true] {
            presence = endedByProcess ? .present([:]) : .absent
            if endedByProcess {
                currentProcess = .init(pid: process.pid, parent: 0, seconds: 2, microseconds: 0, executable: "/fixture")
            }
            do {
                _ = try execution.scopes.fixedInputWindow(binding)
                preconditionFailure("confirmed original lifetime loss must propagate")
            } catch let error as DriverError { precondition(error.code == .staleTarget) }
        }
        // A separate AX-only binding keeps its existing exact-object route;
        // it does not borrow the ended native binding or require WindowServer.
        advertisedID = nil
        let axRow = try state.applications.windows(app).first!
        let axScope = state.applications.observationScope(app, window: refreshed)
        let axSurface = NativeDirectory.Surface(
            owner: app, window: axRow.ref, scope: axScope, parent: nil, element: refreshed, native: nil)
        directory.remember(axSurface)
        let axBinding: [String: Any] = [
            "session": "fixed-window", "instance": state.appSelections.get(owner: app, root: axRow.ref).id,
            "rootWindow": axRow.ref, "surface": axScope, "node": NSNull(), "capture": NSNull()]
        let axFixed = try execution.scopes.fixedInputWindow(axBinding)
        precondition(axFixed.map { CFEqual($0.window, refreshed) } == true)
        directory.remember(.init(
            owner: app, window: axRow.ref, scope: axScope, parent: nil,
            element: AXUIElementCreateApplication(1_000_023), native: nil))
        let differentProxy = try execution.scopes.fixedInputWindow(axBinding)
        precondition(differentProxy == nil, "AX-only identity cannot adopt a different native proxy")
    }

    @MainActor static func unauthorizedForeground() async throws {
        let unit = NativeInputUnit.action("AXPress", nil)
        let wire = unit.declaration(id: "press", supported: true, readiness: .init([("receiver", "unknown")]))
        precondition(wire["unit"] as? String == "native_action")
        precondition(wire["backgroundPreparation"] as? String == "yes")
        let probe = DocumentProbe(mode: "unauthorized")
        let succeeded = try await probe.run(explicit: true)
        precondition(!succeeded && probe.presses == 0 && probe.document.foregrounds == 0)
    }

    @MainActor static func retainedRequirement() throws {
        let receiver = DocumentReceiver()
        let retained = NativeActionDocumentContract(receiver)
        let initial = try retained.observe()
        precondition(initial == .other)
        receiver.unknown = true
        let missing = try retained.observe()
        precondition(missing == .unavailable)
        precondition(retained.selectionRequired)
        do {
            try NativeActionDocumentPolicy.checkDispatch(retained.unit, required: retained.selectionRequired)
            preconditionFailure("a missing focus read cannot erase known other-document evidence")
        } catch is NativeSelectionBasis.Changed {}
        receiver.unknown = false
        receiver.selected = true
        let ready = try retained.observe()
        precondition(ready == .target && retained.selectionRequired)
        try NativeActionDocumentPolicy.checkDispatch(retained.unit, required: retained.selectionRequired)
    }
    @MainActor static func selectionResult() throws {
        for status: AXError in [.success, .attributeUnsupported, .noValue, .invalidUIElement] {
            try NativeDocumentContext.checkSelectionResult(status)
        }
        do {
            try NativeDocumentContext.checkSelectionResult(.cannotComplete)
            preconditionFailure("uncertain native setter must not become foreground retry authority")
        } catch let error as DriverError { precondition(error.code == .executionUnknown && error.effect == .possible) }
    }

    @MainActor static func declarationRefreshes() throws {
        let probe = DocumentProbe(mode: "selectable")
        let intent = try NativeIntent(["kind": "action", "parameters": ["action": "AXPress"]], mode: "write")
        let op = NativeOperation(
            timeoutMs: 5000, writable: true, intent: intent, target: probe.binding, release: {}, cancelActive: {})
        defer { _ = op.finish() }
        let query = NativeInputQueryScope(
            binding: (probe.binding, [], "original"),
            selection: NativeSelectionBasis(access: probe.state.applications), scope: "window")
        func declarations(_ previous: [NativePrimitiveContract], within: [String] = []) -> NativePrimitiveDeclarations {
            NativePrimitiveDeclarations(
                previous: previous, op: op, query: query, within: within, actuator: nil, receiver: nil, basis: [])
        }
        let unit = NativeInputUnit.action("AXPress", nil)
        let first = declarations([])
        let original = try first.actionDocument(for: unit, facts: probe.nativeActionFacts("original"))!
        precondition(original.selectionRequired)
        first.declare(unit, supported: true, enabled: true, actionDocument: original) { _ in
            NativeReadiness([("receiver", "unknown")])
        }
        let firstID = first.current[0].id
        let missing = declarations(try first.registeredContracts())
        let noCapabilities = try missing.registeredContracts()
        precondition(noCapabilities.isEmpty, "an absent action must not retain an executable primitive")
        probe.document.unknown = true
        let facts = NativeElementControl.ActionFacts(
            role: "AXButton", actions: [.init(name: "AXPress", description: nil)], enabled: true,
            applicationMenuContext: false, foreground: true)
        let restored = declarations(noCapabilities)
        let retained = try restored.actionDocument(for: unit, facts: facts)!
        precondition(retained === original && retained.selectionRequired)
        restored.declare(unit, supported: true, enabled: true, actionDocument: retained) { _ in
            NativeReadiness([("receiver", "unknown")])
        }
        precondition(restored.current[0].id != firstID)
        precondition(restored.primitives[0]["backgroundPreparation"] as? String == "yes")
        precondition(restored.primitives[0]["pending"] as? [String] == ["receiver"])
        // Fact collection owns metadata only, even when a low-level caller
        // inspects capabilities in an otherwise read-only operation.
        let readIntent = try NativeIntent(["kind": "read", "parameters": [:]], mode: "read")
        let readOnly = NativeOperation(
            timeoutMs: 5000, writable: false, intent: readIntent, target: probe.binding, release: {}, cancelActive: {})
        defer { _ = readOnly.finish() }
        let readFacts = NativePrimitiveDeclarations(
            previous: [], op: readOnly, query: query, within: [], actuator: nil, receiver: nil, basis: [])
        let observed = try readFacts.actionDocument(for: unit, facts: probe.nativeActionFacts("original"))
        precondition(observed != nil && probe.document.selections == 0)
        let differentScope = declarations([], within: ["anotherContainer"])
        let unrelated = try differentScope.actionDocument(for: unit, facts: facts)
        precondition(unrelated == nil, "original evidence must not cross within scopes")
        let neverConflicted = NativeActionDocumentContract(probe.document)
        probe.document.unknown = false
        probe.document.selected = true
        try neverConflicted.observe()
        probe.document.selected = false
        do {
            try NativeActionDocumentPolicy.checkDispatch(
                neverConflicted.unit, required: false, retained: neverConflicted)
            preconditionFailure("a newly observed competing document must stop dispatch")
        } catch is NativeSelectionBasis.Changed {}
        precondition(neverConflicted.selectionRequired, "final guard observations belong to the same owner")
        probe.document.unknown = true
        do {
            try NativeActionDocumentPolicy.checkDispatch(
                neverConflicted.unit, required: false, retained: neverConflicted)
            preconditionFailure("unknown cannot erase a final-barrier competing document")
        } catch is NativeSelectionBasis.Changed {}
    }

}

@MainActor private final class DocumentReceiver: NativeActionDocumentInput {
    var foreground = true
    var selected = false
    var unknown = false
    var canSelectBackground = true
    var selectionUncertain = false
    var a = 0, b = 0, selections = 0, foregrounds = 0
    var trace: (String) -> Void = { _ in }
    var onFocusRead: () -> Void = {}
    func focus() throws -> NativeDocumentContext.Focus {
        onFocusRead()
        return unknown ? .unavailable : selected ? .target : .other
    }
    func select(
        requireSelection: Bool, check: () throws -> Void, onPreparation: (String) -> Void,
        onNativePreparation: (NativePreparationEffect) -> Void
    ) throws {
        if try focus() == .target { return }
        try check()
        trace("select")
        onPreparation("native document context AXMain")
        onNativePreparation(.init(kind: .windowSelection, phase: .willSend, detail: "AXMain", window: 1))
        selections += 1
        if selectionUncertain {
            throw DriverError(
                "document selection timed out; effect unknown", code: .executionUnknown, effect: .possible)
        }
        if foreground || canSelectBackground { selected = true }
        onNativePreparation(.init(kind: .windowSelection, phase: .returned, detail: "AXMain", window: 1))
        try check()
    }
    func prepareForeground(
        check: () throws -> Void, onPreparation: (String) -> Void, onCleanupUnknown: @escaping @MainActor () -> Void,
        onNativePreparation: (NativePreparationEffect) -> Void, onWait: (NativePreparationWait) -> Void
    ) async throws {
        try check()
        foregrounds += 1
        foreground = true
        onPreparation("action document foreground preparation")
        try check()
    }
}

@MainActor
private final class DocumentProbe: NativeControlObjectAccess, NativeInputUnitAccess, NativeScopeAccess,
    NativeContractLookup, NativeApplicationContextInput
{
    let mode: String
    let document = DocumentReceiver()
    let state = NativeDesktopState()
    var binding: [String: Any] = [
        "session": "s", "instance": "app", "rootWindow": "0", "surface": "window", "node": "original",
        "capture": NSNull()
    ]
    var presses = 0, localPrepared = false, uncertain = false, enteringDispatch = false
    var beforeSelectionChecks = 0, afterSelectionChecks = 0, finalEnabledChecks = 0, completedPreparations = 0
    var localPreparationCalls = 0
    var trace = [String]()
    var actionName: String { ["custom", "customDisabled"].contains(mode) ? "AXShowMenu" : "AXPress" }
    var needsWindowPreparation: Bool {
        [
            "disabledUntilSelected", "stillDisabled", "disabledModal", "disabledTarget", "disabledWithin",
            "disabledBeforeModal", "disabledBeforeTarget", "disabledBeforeWithin"
        ].contains(mode)
    }
    var hasLocalActionContext: Bool {
        needsWindowPreparation
            || [
                "background", "restoreOther", "initialTargetDisabled", "unknownOnlyDisabled", "localRestoresTarget",
                "restoreTargetAfterFailure"
            ].contains(mode)
    }
    var enabled: Bool {
        if ["stillDisabled", "initialTargetDisabled", "unknownOnlyDisabled", "customDisabled"].contains(mode) {
            return false
        }
        return needsWindowPreparation ? document.selected : true
    }
    init(mode: String) {
        self.mode = mode
        document.trace = { [weak self] in self?.trace.append($0) }
        if [
            "background", "restoreOther", "fallback", "unavailable", "unauthorized", "readyBeforeForeground",
            "localRestoresTarget", "restoreTargetAfterFailure"
        ].contains(mode) {
            document.foreground = false
        }
        if ["initialTarget", "restoreOther", "custom", "initialTargetDisabled", "customDisabled"].contains(mode) {
            document.selected = true
        }
        if ["unknownOnly", "unknownOnlyDisabled"].contains(mode) { document.unknown = true }
        if ["fallback", "unavailable", "restoreTargetAfterFailure"].contains(mode) {
            document.canSelectBackground = false
        }
        if mode == "uncertainSelection" { document.selectionUncertain = true }
        if mode == "windowless" { binding["rootWindow"] = NSNull() }
    }
    func boundaryCheck() throws {
        if document.selections == 0 { beforeSelectionChecks += 1 } else { afterSelectionChecks += 1 }
        if mode == "disabledBeforeModal" { throw DriverError("known modal", code: .blockedByModal) }
        if ["disabledBeforeTarget", "disabledBeforeWithin"].contains(mode) {
            throw DriverError("original scope changed", code: .staleTarget)
        }
        guard document.selections > 0 else { return }
        if ["disabledTarget", "disabledWithin"].contains(mode) {
            throw DriverError("original scope changed", code: .staleTarget)
        }
        if mode == "disabledModal" { throw DriverError("known modal", code: .blockedByModal) }
        if mode == "modal" { throw DriverError("known modal", code: .blockedByModal) }
        if mode == "target" || mode == "within" { throw DriverError("original scope changed", code: .staleTarget) }
        if mode == "cancel" { throw CancellationError() }
    }
    func prepare(
        requirement: NativeApplicationContextUnit.Requirement,
        check: @escaping @MainActor () throws -> Void, onPreparation: @escaping @MainActor (String) -> Void,
        onCleanupUnknown: @escaping @MainActor () -> Void,
        onNativePreparation: @escaping @MainActor (NativePreparationEffect) -> Void
    ) async throws -> NativeApplicationContextUnit.Outcome {
        try check()
        localPreparationCalls += 1
        if mode == "restoreTargetAfterFailure", localPreparationCalls == 2 { document.selected = true }
        if !localPrepared {
            trace.append("local")
            localPrepared = true
            if mode == "restoreOther" { document.selected = false }
            if mode == "localRestoresTarget" { document.selected = true }
            onPreparation("process-local activity")
        }
        try check()
        return .activityObserved
    }
    func run(explicit: Bool) async throws -> Bool {
        let parameters: [String: Any] = explicit ? ["action": actionName] : ["button": "left", "count": 1]
        let intent = try NativeIntent(["kind": explicit ? "action" : "click", "parameters": parameters], mode: "write")
        let op = NativeOperation(
            timeoutMs: 30_000, writable: true, intent: intent, target: binding, clickContract: explicit ? nil : "v2",
            release: {}, cancelActive: {})
        defer { _ = op.finish() }
        let retained: NativeActionDocumentContract? =
            ["windowless", "custom", "customDisabled", "lateDocument"].contains(mode)
            ? nil : NativeActionDocumentContract(document)
        try retained?.observe()
        if mode == "readyBeforeForeground" { document.selected = true }
        let documentScope = NativeActionDocumentContexts.Scope(
            node: "original", surface: "window", within: [], actuator: nil, receiver: nil)
        func assertNoForegroundAuthority(_ receipt: NativeExecutionReceipt, explicit: Bool) throws {
            precondition(receipt.retryReason == "preconditionChanged")
            do {
                if explicit {
                    try op.requireForegroundPreparation("press")
                } else {
                    try op.requireClickForeground(
                        target: binding, within: [], backgroundFamily: "macos.axAction." + actionName,
                        backgroundUnavailable: false)
                }
                preconditionFailure("a final context change must not authorize foreground activation")
            } catch let error as DriverError { precondition(error.code == .invalidRequest) }
            if mode == "lateDocument" {
                let evidence = op.actionDocuments.context(documentScope, observed: nil)
                precondition(evidence?.selectionRequired == true, "late final evidence must remain operation-owned")
            }
        }
        let boundary = NativeUnitBoundary(
            checkConditions: boundaryCheck, checkSelection: {}, markDispatchInFlight: {},
            beforeDispatch: {
                try self.boundaryCheck()
                op.recordInputDispatch()
            })
        let directory = NativeDirectory(state: state, session: "s")
        let access = NativeControlAccess(
            scopes: self, units: self, captures: NativeCapture(directory: directory, sharing: state.sharing),
            desktop: { preconditionFailure("desktop input was not selected") }, objects: self, sharing: state.sharing,
            contracts: self, recordEffect: {})
        let contract = NativePrimitiveContract(
            id: "press", node: "original", within: [], actuator: nil, receiver: nil, basis: [],
            unit: .action(actionName, nil), actionDocument: retained)
        func command(_ kind: String, foreground: Bool) throws -> NativeUnitCommand {
            try NativeUnitCommand(["kind": kind, "primitiveId": "press", "target": binding, "foreground": foreground])
        }
        func route(_ kind: String, foreground: Bool, receipt: NativeExecutionReceipt) throws -> SelectedNativeRoute {
            try NativeObjectActionPipeline.select(
                NativeRouteContext(
                    command: command(kind, foreground: foreground), operation: op,
                    resolved: .init(contract: contract, binding: (binding, [], "original")), boundary: boundary,
                    receipt: receipt, access: access), name: actionName, description: nil)
        }
        func prepareExplicit(_ foreground: Bool) async throws {
            let receipt = NativeExecutionReceipt(target: binding, onEffect: {})
            do {
                guard
                    case .prepare(let prepare) = try route("prepare_receiver", foreground: foreground, receipt: receipt)
                else { preconditionFailure("the native action declares a real preparation entry") }
                try await NativePreparePipeline.run(prepare, boundary: boundary, op: op, id: "press")
                completedPreparations += 1
            } catch {
                BuildNativeFailureReceiptPass.run(
                    error, kind: "prepare_receiver", foreground: foreground, id: "press", op: op, receipt: receipt)
                if ["fallback", "restoreTargetAfterFailure"].contains(mode) {
                    precondition(receipt.retryReason == "preparationUnavailable")
                }
                throw error
            }
        }
        func click(_ foreground: Bool) async throws {
            let receipt = NativeExecutionReceipt(target: binding, onEffect: {})
            let evidence = NativeClickEvidence(target: binding, family: "macos.axAction." + actionName)
            let offer = NativeClickContract(
                routeFamily: "macos.axAction." + actionName, delivery: .objectAction, admission: .preparationRequired,
                reason: nil, preparationOptions: ["background", "foreground"],
                backgroundFamily: "macos.axAction." + actionName)
            let plan = NativeClickPlan(
                target: .action(node: "original", name: actionName, description: nil), offer: offer, pointer: nil,
                command: try command("native_action", foreground: foreground), operation: op, within: [], step: 1,
                document: retained?.unit,
                documentPreparation: .init(windowSelectionRequired: retained?.selectionRequired == true),
                actionDocument: retained, documentScope: documentScope,
                actionContext: hasLocalActionContext ? self : nil)
            do {
                try await NativeClickPreparationPipeline.run(
                    plan, boundary: boundary, receipt: receipt, evidence: evidence, objects: self)
                completedPreparations += 1
                try await DispatchNativeClickPass.run(
                    plan, boundary: boundary, receipt: receipt, evidence: evidence, objects: self)
            } catch {
                BuildNativeClickFailurePass.run(
                    error, command: plan.command, operation: op, step: 1, offer: offer, within: [], receipt: receipt,
                    evidence: evidence)
                if ["switched", "unknownAfter", "lateDocument"].contains(mode) {
                    try assertNoForegroundAuthority(receipt, explicit: false)
                }
                throw error
            }
        }
        do {
            if explicit {
                if mode == "unauthorized" { try await prepareExplicit(true) }
                if retained?.selectionRequired == true {
                    do { try await prepareExplicit(false) } catch is NativePreparationUnavailable
                        where ["fallback", "restoreTargetAfterFailure"].contains(mode)
                    { try await prepareExplicit(true) }
                }
                let receipt = NativeExecutionReceipt(target: binding, onEffect: {})
                guard case .dispatch(let dispatch) = try route("native_action", foreground: false, receipt: receipt)
                else { preconditionFailure("explicit action must dispatch") }
                _ = try await dispatch()
            } else {
                do {
                    try await click(["readyBeforeForeground", "localRestoresTarget"].contains(mode))
                } catch is NativePreparationUnavailable where ["fallback", "restoreTargetAfterFailure"].contains(mode) {
                    try await click(true)
                }
            }
            return true
        } catch {
            if explicit && ["switched", "unknownAfter", "lateDocument"].contains(mode) {
                let receipt = NativeExecutionReceipt(target: binding, onEffect: {})
                BuildNativeFailureReceiptPass.run(
                    error, kind: "native_action", foreground: false, id: "press", op: op, receipt: receipt)
                try assertNoForegroundAuthority(receipt, explicit: true)
            }
            uncertain = (error as? ClassifiedFailure)?.code == .executionUnknown
            return false
        }
    }
    func retainNode(_ ref: String) -> (() -> Void)? { {} }
    func nativeActionFacts(_ ref: String) throws -> NativeElementControl.ActionFacts {
        .init(
            role: kAXButtonRole,
            actions: mode == "action" && document.selections > 0 ? [] : [.init(name: actionName, description: nil)],
            enabled: enabled, applicationMenuContext: false, foreground: document.foreground,
            documentEvidence: ["windowless", "custom", "customDisabled"].contains(mode)
                || (mode == "lateDocument" && !enteringDispatch) ? .unknown : .standard(document))
    }
    func nativeExplicitActionFacts(_ ref: String, request: String) throws -> NativeElementControl.ActionFacts {
        try nativeActionFacts(ref)
    }
    func executeNativeAction(
        _ ref: String, action: String, description: String?, onDispatch: @MainActor () throws -> Void,
        onReturned: @MainActor () -> Void
    ) throws {
        guard try nativeActionFacts(ref).actions?.isEmpty == false else {
            throw DriverError("action changed", code: .staleTarget)
        }
        finalEnabledChecks += 1
        guard enabled else { throw DriverError("selected control is disabled", code: .inputUnavailable) }
        enteringDispatch = true
        if mode == "switched" { document.selected = false }
        if mode == "unknownAfter" { document.unknown = true }
        try onDispatch()
        presses += 1
        if document.selected || ["windowless", "unknownOnly"].contains(mode) {
            document.a += 1
        } else {
            document.b += 1
        }
        onReturned()
        if mode == "unconfirmedAction" {
            throw DriverError("action return unknown", code: .executionUnknown, effect: .possible)
        }
    }
    func executeNativeExplicitAction(
        _ ref: String, request: String, action: String, onDispatch: @MainActor () throws -> Void,
        onReturned: @MainActor () -> Void
    ) throws {
        try executeNativeAction(ref, action: action, description: nil, onDispatch: onDispatch, onReturned: onReturned)
    }
    func executeNativeSelection(
        _ ref: String, contract: NativeRowSelection.Contract, onDispatch: @MainActor () throws -> Void,
        onReturned: @MainActor () -> Void
    ) throws -> NativeElementControl.ActionResult { preconditionFailure("selection was not selected") }
    func executeNativeTextValue(
        _ ref: String, contract: NativeElementControl.TextValueContract, text: String,
        onDispatch: @MainActor () throws -> Void, originalWindow: () throws -> AXUIElement?
    ) throws -> NativeElementControl.ActionResult { preconditionFailure("text was not selected") }
    func actionContextUnit(_ binding: [String: Any], action: String) throws -> (any NativeApplicationContextInput)? {
        hasLocalActionContext ? self : nil
    }
    func receiverRelation(
        _ binding: [String: Any], actuator: NativeInspectQuery.Actuator?, receiver: NativeInspectQuery.Receiver?
    ) throws -> NativeReceiverRelation? { nil }
    func keyboardUnit(
        _ binding: [String: Any], actuator: NativeInspectQuery.Actuator?, receiver: NativeInspectQuery.Receiver?
    ) throws -> NativeKeyboardUnit { preconditionFailure("unselected") }
    func selectedTextUnit(
        _ binding: [String: Any], actuator: NativeInspectQuery.Actuator?, receiver: NativeInspectQuery.Receiver?
    ) throws -> NativeSelectedTextUnit { preconditionFailure("unselected") }
    func elementPointerUnit(_ binding: [String: Any], actuator: NativeInspectQuery.Actuator?) throws
        -> NativeElementPointerUnit
    { throw DriverError("test has no pointer fallback", code: .inputUnavailable) }
    func menuUnit(_ binding: [String: Any], within: [String]) throws -> NativeMenuUnit {
        preconditionFailure("unselected")
    }
    func scrollUnit(_ binding: [String: Any], actuator: NativeInspectQuery.Actuator) throws -> NativeScrollUnit {
        preconditionFailure("unselected")
    }
    func inputBinding(_ raw: Any?, input: Bool, attachedMenuAction: Bool) throws -> (
        wire: [String: Any], blocked: [String], node: String?
    ) {
        try boundaryCheck()
        return (binding, [], "original")
    }
    func checkContainment(_ binding: [String: Any], within: [String], op: NativeOperation) throws {
        try boundaryCheck()
    }
    func checkActuator(_ binding: [String: Any], actuator: NativeInspectQuery.Actuator?, op: NativeOperation) throws {
        try boundaryCheck()
    }
    func isDesktop(_ binding: [String: Any]) -> Bool { false }
    func directoryTarget(_ raw: Any?) throws -> [String: Any]? { nil }
    func observationTarget(_ raw: Any?) throws -> (wire: [String: Any], node: String) { (binding, "original") }
    func observePointer(_ binding: [String: Any]) {}
    func aimPointer(_ binding: [String: Any], intent: NativeIntent, receiver: String?) {}
    func primitiveContracts(_ token: String) -> [NativePrimitiveContract] { [] }
    func selectionBasis(_ token: String) -> NativeSelectionBasis { NativeSelectionBasis(access: state.applications) }
}

/// Only unselected query ports are stubs. Both real sampling passes and the
/// original NativePrimitiveDeclarations own admission; this test has no router.
extension DocumentProbe: NativeQueryObjectAccess, NativeContractAccess {
    func nativeFacts(
        _ ref: String, session: String, fields: [String], maxNodes: Int, maxDepth: Int,
        childrenPage: NativeInspectQuery.ChildrenPage?, relationPage: NativeInspectQuery.RelationPage?,
        advertisedFields: [String], followRelations: [String], textContent: Bool, actionDetail: NativeActionDetail,
        focusedElement: Bool
    ) throws -> [String: Any] { preconditionFailure("unselected query") }
    func nativeRelation(_ ref: String, session: String, property: String, cursor: String?, limit: Int) throws
        -> [String: Any]
    { preconditionFailure("unselected relation") }
    func readText(_ ref: String, offset: Int, length: Int) throws -> [String: Any] {
        preconditionFailure("unselected text")
    }
    func registerNativeRoot(_ element: AXUIElement, app: NSRunningApplication, scope: String) throws -> String {
        preconditionFailure("unselected root")
    }
    func nativeSelectionContract(_ ref: String) throws -> NativeRowSelection.Contract? { nil }
    func nativeTextValueFacts(
        _ ref: String, replacementText: String, originalWindow: () throws -> AXUIElement?
    ) throws -> NativeElementControl.TextValueFacts {
        preconditionFailure("unselected edit")
    }
    func replacePrimitiveContracts(_ token: String, _ values: [NativePrimitiveContract]) {}

    func declaration(explicit: Bool) throws -> [String: Any] {
        let parameters: [String: Any] = explicit ? ["action": actionName] : ["button": "left", "count": 1]
        let intent = try NativeIntent(["kind": explicit ? "action" : "click", "parameters": parameters], mode: "write")
        let op = NativeOperation(
            timeoutMs: 5000, writable: true, intent: intent, target: binding, clickContract: explicit ? nil : "v2",
            release: {}, cancelActive: {})
        defer { _ = op.finish() }
        let query = NativeInputQueryScope(
            binding: (binding, [], "original"),
            selection: NativeSelectionBasis(access: state.applications), scope: "window")
        let plan = NativeInputContextPlan(
            query: query, operation: op, within: [], actuator: nil, receiver: nil, basis: [])
        let declarations = NativePrimitiveDeclarations(
            previous: [], op: op, query: query, within: [], actuator: nil, receiver: nil, basis: [])
        let directory = NativeDirectory(state: state, session: "s")
        let access = NativeQueryAccess(
            session: "s", scopes: self, units: self,
            captures: NativeCapture(directory: directory, sharing: state.sharing),
            desktop: { preconditionFailure("desktop is not selected") }, objects: self, directory: directory,
            sharing: state.sharing, contracts: self)
        let sampled =
            try explicit
            ? SampleNativeInputRoutesPass.run(plan, declarations: declarations, access: access)
            : SampleNativeClickOffersPass.run(plan, declarations: declarations, access: access)
        try RegisterNativeInputContractsPass.run(sampled, access: self)
        let encoded = EncodeNativeInputContextPass.run(sampled)
        let actions = declarations.primitives.filter { $0["unit"] as? String == "native_action" }
        precondition(actions.count == 1, "exactly one original action declaration expected")
        return [
            "rawEnabled": encoded["enabled"]!, "primitive": actions[0], "selectionCount": document.selections,
            "localPrepared": localPrepared
        ]
    }
}

extension NativeActionDocumentTests {
    @MainActor static func stagedEnabledPreparation() async throws {
        for explicit in [false, true] {
            for mode in [
                "disabledUntilSelected", "stillDisabled", "initialTargetDisabled", "unknownOnlyDisabled",
                "customDisabled", "disabledModal", "disabledTarget", "disabledWithin", "disabledBeforeModal",
                "disabledBeforeTarget", "disabledBeforeWithin"
            ] {
                let sampled = DocumentProbe(mode: mode)
                let declaration = try sampled.declaration(explicit: explicit)
                let primitive = declaration["primitive"] as! [String: Any]
                let offer = primitive["click"] as? [String: Any]
                let needsPrepare = sampled.needsWindowPreparation
                let pending = primitive["pending"] as? [String] ?? []
                precondition(declaration["rawEnabled"] as? String == "no", "raw disabled must remain observable")
                precondition(
                    declaration["selectionCount"] as? Int == 0 && declaration["localPrepared"] as? Bool == false,
                    "sampling must not prepare a disabled target")
                if needsPrepare {
                    precondition(primitive["supported"] as? String == "yes", "known window preparation was lost")
                    precondition(primitive["backgroundPreparation"] as? String == "yes")
                    precondition(!pending.contains("enabled"), "dispatch-only enabled blocked preparation")
                    if explicit {
                        precondition(pending == ["receiver"])
                    } else {
                        precondition(offer?["admission"] as? String == "preparationRequired")
                        precondition((offer?["preparationOptions"] as? [String] ?? []).contains("background"))
                    }
                } else if explicit {
                    precondition(primitive["supported"] as? String == "no" || pending.contains("enabled"))
                } else {
                    precondition(["unavailable", "insufficientEvidence"].contains(offer?["admission"] as? String ?? ""))
                    precondition((offer?["preparationOptions"] as? [String] ?? []).isEmpty)
                }

                // Isolate native preparation from admission: a corrected offer
                // must not hide another enabled guard before window selection.
                let native = DocumentProbe(mode: mode)
                precondition(!native.enabled)
                let succeeded = try await native.run(explicit: explicit)
                let enabledAfterSelection = mode == "disabledUntilSelected"
                let blockedBeforeSelection = mode.hasPrefix("disabledBefore")
                let expectedSelections = needsPrepare && !blockedBeforeSelection ? 1 : 0
                let expectedActions = enabledAfterSelection ? 1 : 0
                precondition(succeeded == enabledAfterSelection, "wrong preparation result: \(explicit) \(mode)")
                precondition(native.document.selections == expectedSelections, "wrong preparation count: \(mode)")
                precondition(native.presses == expectedActions && native.document.a == expectedActions)
                precondition(native.document.b == 0 && native.document.foregrounds == 0)
                if needsPrepare {
                    precondition(native.beforeSelectionChecks > 0, "scope must be checked before selection")
                    if !blockedBeforeSelection {
                        precondition(native.afterSelectionChecks > 0, "scope must be checked after selection")
                    }
                }
                if blockedBeforeSelection || !needsPrepare {
                    precondition(!native.localPrepared, "unavailable preparation received local activity")
                }
                if enabledAfterSelection { precondition(native.finalEnabledChecks == 1) }
                if mode == "stillDisabled" {
                    precondition(
                        !native.enabled && native.document.selections == 1 && native.presses == 0,
                        "zero preparation cannot satisfy the still-disabled regression")
                }
                if enabledAfterSelection || mode == "stillDisabled" {
                    precondition(native.completedPreparations == 1, "independent preparation must complete")
                    let refreshed = try native.declaration(explicit: explicit)
                    precondition(refreshed["rawEnabled"] as? String == (enabledAfterSelection ? "yes" : "no"))
                    let refreshedPrimitive = refreshed["primitive"] as! [String: Any]
                    precondition(refreshedPrimitive["supported"] as? String == (enabledAfterSelection ? "yes" : "no"))
                    if explicit {
                        precondition(!(refreshedPrimitive["pending"] as? [String] ?? []).contains("receiver"))
                    } else if mode == "stillDisabled" {
                        let refreshedOffer = refreshedPrimitive["click"] as! [String: Any]
                        precondition(refreshedOffer["admission"] as? String == "unavailable")
                        precondition((refreshedOffer["preparationOptions"] as? [String] ?? []).isEmpty)
                    }
                }
            }
        }
    }

    @MainActor static func skipsPreparedDocument() async throws {
        for explicit in [false, true] {
            for mode in ["readyBeforeForeground", "localRestoresTarget", "restoreTargetAfterFailure"] {
                let probe = DocumentProbe(mode: mode)
                let succeeded = try await probe.run(explicit: explicit)
                precondition(succeeded && probe.presses == 1 && probe.document.a == 1 && probe.document.b == 0)
                precondition(
                    probe.document.selections == (mode == "restoreTargetAfterFailure" ? 1 : 0)
                        && probe.document.foregrounds == 0,
                    "a satisfied original-window obligation cannot request foreground preparation")
                precondition(probe.localPrepared == (mode != "readyBeforeForeground"))
                if mode == "restoreTargetAfterFailure" { precondition(probe.localPreparationCalls >= 2) }
            }
        }
    }

    final class DocumentApplication: NSRunningApplication, @unchecked Sendable {
        override var processIdentifier: pid_t { getpid() }
    }

    @MainActor static func documentPreparationBoundary() async throws {
        let application = DocumentApplication()
        let target = NativeProcessIdentity.read(application.processIdentifier)!
        let original = NativeProcessIdentity(pid: 101, parent: 1, seconds: 1, microseconds: 0, executable: "/owned/original")
        var foreground: pid_t? = original.pid
        let owner = NativeForegroundResponsibility(
            snapshot: { .init(foregroundPID: foreground, activity: [0]) },
            process: { $0 == original.pid ? original : $0 == target.pid ? target : nil },
            window: { _ in nil }, watchActivation: false)
        let opener = DocumentReceiver()
        try await NativeForegroundResponsibility.$current.withValue(owner) {
            try await NativeForegroundRestorationScope.perform(
                target: BackgroundTarget(application), onPreparation: { _ in }, afterInput: true,
                inputState: { .completed }, presentation: { .present }
            ) {
                try await NativeDocumentActionPreparationPipeline.run(
                    opener, foreground: false, authorizeForeground: {}, check: {}, onPreparation: { _ in },
                    onCleanupUnknown: {}, onNativePreparation: { event in
                        if event.phase == .willSend {
                            precondition(owner.original == original,
                                         "document selection must claim original foreground before AXMain")
                            foreground = target.pid
                        }
                    }, onWait: { _ in })
                NativeForegroundRestorationScope.current?.willDispatchInput()
                precondition(owner.hasForegroundObligation,
                             "actual document preparation excursion must be sampled before result inspection")
            }
        }
        precondition(owner.retainedPresentation != nil && owner.suspend(),
                     "the opened menu must retain the first document's restoration responsibility")
        owner.stop()
        let ready = DocumentReceiver()
        ready.foreground = false
        ready.selected = true
        var trace = [String]()
        ready.onFocusRead = { trace.append("focus") }
        try await NativeDocumentActionPreparationPipeline.run(
            ready, foreground: true,
            authorizeForeground: { preconditionFailure("a ready document needs no activation") },
            check: { trace.append("check") }, onPreparation: { _ in preconditionFailure("unexpected preparation") },
            onCleanupUnknown: {}, onNativePreparation: { _ in preconditionFailure("unexpected native preparation") },
            onWait: { _ in })
        precondition(trace == ["check", "focus", "check"])
        precondition(ready.selections == 0 && ready.foregrounds == 0 && !ready.foreground)

        for stopAfterFocus in [false, true] {
            let invalid = DocumentReceiver()
            invalid.foreground = false
            var checked = 0, queried = 0
            invalid.onFocusRead = { queried += 1 }
            do {
                try await NativeDocumentActionPreparationPipeline.run(
                    invalid, foreground: true, authorizeForeground: { preconditionFailure("invalid scope activated") },
                    check: {
                        checked += 1
                        if checked == (stopAfterFocus ? 2 : 1) {
                            throw DriverError("original scope changed", code: .blockedByModal)
                        }
                    }, onPreparation: { _ in preconditionFailure("invalid scope prepared") }, onCleanupUnknown: {},
                    onNativePreparation: { _ in preconditionFailure("invalid scope received native preparation") },
                    onWait: { _ in })
                preconditionFailure("a scope fault must stop document preparation")
            } catch let error as DriverError { precondition(error.code == .blockedByModal) }
            precondition(queried == (stopAfterFocus ? 1 : 0) && invalid.selections == 0 && invalid.foregrounds == 0)
        }

        let unknown = DocumentReceiver()
        unknown.unknown = true
        do {
            try await NativeDocumentActionPreparationPipeline.run(
                unknown, foreground: false, authorizeForeground: { preconditionFailure("unknown scope activated") },
                check: {}, onPreparation: { _ in }, onCleanupUnknown: {}, onNativePreparation: { _ in },
                onWait: { _ in })
            preconditionFailure("unknown focus cannot satisfy original document preparation")
        } catch is NativePreparationUnavailable {}
        precondition(unknown.selections == 1 && unknown.foregrounds == 0)
    }
}
