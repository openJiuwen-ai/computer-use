// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices

@main enum NativeApplicationContextTests {
    @MainActor static func main() async throws {
        try eventConstruction()
        try await optionalContext()
        try await actionRoutes()
        print("PASS document-only activity preparation; ordinary AXPress does not activate its application")
    }

    @MainActor static func eventConstruction() throws {
        do {
            let raw = SyntheticFocusEvents.applicationActivation()!
            let event = NSEvent(cgEvent: raw)!
            precondition(event.type == .appKitDefined)
            precondition(raw.timestamp > 0)
            precondition(event.subtype.rawValue == 1)
            precondition(event.windowNumber == 0 && event.modifierFlags.isEmpty && event.data1 == 0 && event.data2 == 0)
        }
    }

    @MainActor static func optionalContext() async throws {
        let cases: [(pid_t?, Bool?, Bool, Bool, Int)] = [
            (101, false, false, true, 0), (nil, false, false, true, 0), (999, true, false, true, 0),
            (999, nil, true, true, 0), (999, false, false, false, 0), (999, false, false, true, 1),
            (999, nil, false, true, 1), (999, false, true, true, 1)
        ]
        for (foreground, active, owned, available, expected) in cases {
            var posts = 0, waits = 0, fullChecks = 0, pollChecks = 0
            _ = try await NativeApplicationContextUnit.prepare(
                pid: 101, sample: { .init(foregroundPID: foreground, reportedActive: active) },
                alreadyPosted: { owned },
                make: { available ? SyntheticFocusEvents.applicationActivation() : nil },
                post: { _ in posts += 1 }, check: { fullChecks += 1 }, pollCheck: { pollChecks += 1 },
                onPreparation: { _ in }, onNativePreparation: { _ in }, wait: { waits += 1 })
            precondition(posts == expected)
            if posts == 1 && active == false {
                precondition(
                    waits == 2 && fullChecks == 4 && pollChecks == 6,
                    "an optional ignored ACK gets only two short queue turns, without repeated full scope discovery")
            }
        }
        var reported = false, posts = 0
        let outcome = try await NativeApplicationContextUnit.prepare(
            pid: 101, sample: { .init(foregroundPID: 999, reportedActive: reported) }, alreadyPosted: { false },
            make: { SyntheticFocusEvents.applicationActivation() },
            post: { _ in
                posts += 1
                reported = true
            }, check: {}, onPreparation: { _ in }, onNativePreparation: { _ in }, wait: {})
        precondition(outcome == .activityObserved && posts == 1)
        var turns = 0
        let eventual = try await NativeApplicationContextUnit.prepare(
            pid: 101, sample: { .init(foregroundPID: 999, reportedActive: turns == 2) }, alreadyPosted: { false },
            make: { SyntheticFocusEvents.applicationActivation() }, post: { _ in }, check: {},
            onPreparation: { _ in }, onNativePreparation: { _ in }, wait: { turns += 1 })
        precondition(eventual == .activityObserved && turns == 2,
                     "positive activity arriving on a short queue turn remains observable")
        let ordinary = NativeActionDocumentPolicy.preparation(
            action: "AXPress", exactStandardWindow: true, otherDocument: false)
        let other = NativeActionDocumentPolicy.preparation(
            action: "AXPress", exactStandardWindow: true, otherDocument: true)
        precondition(!ordinary.required && other.windowSelectionRequired)
        precondition(!NativeDocumentContext.permitsDispatch(.unavailable, prepared: true))
        precondition(!NativeDocumentContext.permitsDispatch(.other, prepared: true))
        precondition(NativeDocumentContext.permitsDispatch(.target, prepared: true))
        precondition(
            NativeObjectActionPolicy.applicationContextEligible(
                action: "AXPress", hasWindow: true, applicationMenu: false))
        precondition(
            !NativeObjectActionPolicy.applicationContextEligible(
                action: "AXShowMenu", hasWindow: true, applicationMenu: false))
        precondition(
            !NativeObjectActionPolicy.applicationContextEligible(
                action: "AXPress", hasWindow: false, applicationMenu: false))
        precondition(
            !NativeObjectActionPolicy.applicationContextEligible(
                action: "AXPress", hasWindow: true, applicationMenu: true))
    }

    @MainActor static func actionRoutes() async throws {
        for explicit in [false, true] {
            for mode in [
                "observed", "ignored", "unknown", "unavailable", "preexistingActive", "foreground", "modal", "target",
                "within", "action", "cancel", "unconfirmedAction", "windowless", "disabled"
            ] {
                let probe = ContextProbe(mode: mode)
                let result = try await probe.run(explicit: explicit)
                let blocked = ["modal", "target", "within", "action", "cancel", "disabled"].contains(mode)
                precondition(probe.presses == (blocked ? 0 : 1))
                precondition(
                    probe.preparationCalls == 0 && probe.posts == 0,
                    "ordinary AXPress must not inject activation before discovering whether it needs any preparation")
                if blocked || mode == "unconfirmedAction" { precondition(!result) } else { precondition(result) }
                // No public input recipe or second AXPress follows an uncertain action.
                precondition(probe.presses <= 1)
            }
        }
    }
}

@MainActor
private final class ContextProbe: NativeControlObjectAccess, NativeInputUnitAccess, NativeScopeAccess,
    NativeContractLookup, NativeApplicationContextInput
{
    let mode: String
    let state = NativeDesktopState()
    var binding: [String: Any] = [
        "session": "s", "instance": "app", "rootWindow": "0", "surface": "window", "node": "original",
        "capture": NSNull()
    ]
    var posts = 0, presses = 0, preparationCalls = 0
    var active = false
    var samples = 0
    init(mode: String) {
        self.mode = mode
        if mode == "windowless" { binding["rootWindow"] = NSNull() }
    }
    func boundaryCheck() throws {
        if mode == "modal" { throw DriverError("known modal", code: .blockedByModal) }
        if mode == "target" || mode == "within" { throw DriverError("original scope changed", code: .staleTarget) }
        if mode == "cancel" { throw CancellationError() }
    }
    func prepare(
        check: @escaping @MainActor () throws -> Void, onPreparation: @escaping @MainActor (String) -> Void,
        onCleanupUnknown: @escaping @MainActor () -> Void,
        onNativePreparation: @escaping @MainActor (NativePreparationEffect) -> Void
    ) async throws -> NativeApplicationContextUnit.Outcome {
        preparationCalls += 1
        return try await NativeApplicationContextUnit.prepare(
            pid: 101,
            sample: {
                self.samples += 1
                return .init(
                    foregroundPID: self.mode == "foreground" ? 101 : 999,
                    reportedActive: self.mode == "unknown" ? nil : self.mode == "preexistingActive" || self.active)
            }, alreadyPosted: { false },
            make: { self.mode == "unavailable" ? nil : SyntheticFocusEvents.applicationActivation() },
            post: { _ in
                self.posts += 1
                if self.mode != "ignored" && self.mode != "scopeDuringPoll" { self.active = true }
            }, check: check, onPreparation: onPreparation, onNativePreparation: onNativePreparation, wait: {})
    }
    func run(explicit: Bool) async throws -> Bool {
        let parameters: [String: Any] = explicit ? ["action": "AXPress"] : ["button": "left", "count": 1]
        let intent = try NativeIntent(["kind": explicit ? "action" : "click", "parameters": parameters], mode: "write")
        let op = NativeOperation(
            timeoutMs: 30_000, writable: true, intent: intent, target: binding, clickContract: explicit ? nil : "v2",
            release: {}, cancelActive: {})
        defer { _ = op.finish() }
        let command = try NativeUnitCommand([
            "kind": "native_action", "primitiveId": "press", "target": binding, "foreground": false
        ])
        let receipt = NativeExecutionReceipt(target: binding, onEffect: {})
        let evidence = NativeClickEvidence(target: binding, family: "macos.axAction.AXPress")
        let boundary = NativeUnitBoundary(
            checkConditions: boundaryCheck, checkSelection: {}, markDispatchInFlight: {}, checkKeyboardSelection: {},
            markPreparedKeyboardDispatchInFlight: {},
            beforeDispatch: {
                try self.boundaryCheck()
                op.recordInputDispatch()
            })
        do {
            if explicit {
                let directory = NativeDirectory(state: state, session: "s")
                let access = NativeControlAccess(
                    scopes: self, units: self, captures: NativeCapture(directory: directory, sharing: state.sharing),
                    desktop: { preconditionFailure("desktop input was not selected") }, objects: self,
                    sharing: state.sharing, contracts: self, recordEffect: {})
                let contract = NativePrimitiveContract(
                    id: "press", node: "original", within: [], actuator: nil, receiver: nil, basis: [],
                    unit: .action("AXPress", nil))
                let context = NativeRouteContext(
                    command: command, operation: op,
                    resolved: .init(contract: contract, binding: (binding, [], "original")), boundary: boundary,
                    receipt: receipt, access: access)
                let selected = try NativeObjectActionPipeline.select(context, name: "AXPress", description: nil)
                guard case .dispatch(let dispatch) = selected else {
                    preconditionFailure("explicit action must dispatch")
                }
                _ = try await dispatch()
            } else {
                let offer = NativeClickContract(
                    routeFamily: "macos.axAction.AXPress", delivery: .objectAction, admission: .ready, reason: nil,
                    preparationOptions: [])
                let plan = NativeClickPlan(
                    target: .action(node: "original", name: "AXPress", description: nil), offer: offer, pointer: nil,
                    command: command, operation: op, within: [], step: 1,
                    actionContext: mode == "windowless" ? nil : self)
                try await NativeClickPreparationPipeline.run(
                    plan, boundary: boundary, receipt: receipt, evidence: evidence, objects: self)
                try await DispatchNativeClickPass.run(
                    plan, boundary: boundary, receipt: receipt, evidence: evidence, objects: self)
            }
            return true
        } catch { return false }
    }
    func retainNode(_ ref: String) -> (() -> Void)? { {} }
    func nativeActionFacts(_ ref: String) throws -> NativeElementControl.ActionFacts {
        .init(
            role: kAXButtonRole,
            actions: mode == "action" ? [] : [.init(name: "AXPress", description: nil)], enabled: mode != "disabled",
            applicationMenuContext: false, foreground: false)
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
        guard try nativeActionFacts(ref).enabled != false else {
            throw DriverError("action disabled", code: .inputUnavailable)
        }
        try onDispatch()
        presses += 1
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
        precondition(action == "AXPress")
        return mode == "windowless" ? nil : self
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
    { preconditionFailure("unselected") }
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
