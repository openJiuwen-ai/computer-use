// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices

@main enum NativeActionDocumentTests {
    @MainActor static func main() async throws {
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
            binding: (probe.binding, [], "original"), desktop: false,
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
        check: @escaping @MainActor () throws -> Void, onPreparation: @escaping @MainActor (String) -> Void,
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
            checkConditions: boundaryCheck, checkSelection: {}, markDispatchInFlight: {}, checkKeyboardSelection: {},
            markPreparedKeyboardDispatchInFlight: {},
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
        onDispatch: @MainActor () throws -> Void
    ) throws -> NativeElementControl.ActionResult { preconditionFailure("text was not selected") }
    func actionContextUnit(_ binding: [String: Any], action: String) throws -> (any NativeApplicationContextInput)? {
        hasLocalActionContext ? self : nil
    }
    func receiverRelation(
        _ binding: [String: Any], actuator: NativeInspectQuery.Actuator?, receiver: NativeInspectQuery.Receiver?
    ) throws -> NativeReceiverRelation? { nil }
    func desktopUnit(_ binding: [String: Any]) throws -> NativeDesktopUnit { preconditionFailure("unselected") }
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
        advertisedFields: [String], followRelations: [String], textContent: Bool
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
    func nativeTextValueFacts(_ ref: String) throws -> NativeElementControl.TextValueFacts {
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
            binding: (binding, [], "original"), desktop: false,
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

    @MainActor static func documentPreparationBoundary() async throws {
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
