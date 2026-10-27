import AppKit
import ApplicationServices
import Foundation

/// Mechanism tests use the production click pipeline with borrowed object
/// ports and pure journals. No test posts native input or activates a window.
@main struct NativeClickTests {
    @MainActor static let target: [String: Any] = [
        "session": "s", "instance": "i", "rootWindow": "w", "surface": "surface", "node": "original-node",
        "capture": NSNull()
    ]

    @MainActor static func main() async throws {
        NSApplication.shared.setActivationPolicy(.prohibited)
        try negotiationAndOffers()
        rowSelectionEligibility()
        documentContextBoundary()
        try preparationObservation()
        try await preparationContinuation()
        try foregroundAuthority()
        try await selectedActionPipeline()
        try await toggleEffectPipeline()
        print("PASS click v1 negotiation, fixed offers, native attempt, preparation impact, authority and no replay")
    }

    @MainActor static func operation(version: String? = "v1") throws -> NativeOperation {
        NativeOperation(
            timeoutMs: 10000, writable: true,
            intent: try NativeIntent(["kind": "click", "parameters": ["button": "left", "count": 1]], mode: "write"),
            target: target, clickContract: version, release: {}, cancelActive: {})
    }

    @MainActor static func command(id: String = "old-id", foreground: Bool = false, kind: String = "native_action")
        throws -> NativeUnitCommand
    { try NativeUnitCommand(["kind": kind, "target": target, "primitiveId": id, "foreground": foreground]) }

    @MainActor static func negotiationAndOffers() throws {
        let raw: [String: Any] = [
            "mode": "write", "target": target, "intent": ["kind": "click", "parameters": ["button": "left", "count": 1]]
        ]
        let legacyBegin = try NativeBeginRequest(raw)
        precondition(legacyBegin.clickContract == nil)
        var extended = raw
        extended["clickContract"] = "v1"
        let clickBegin = try NativeBeginRequest(extended)
        precondition(clickBegin.clickContract == "v1")
        extended["clickContract"] = "v2"
        let selectionBegin = try NativeBeginRequest(extended)
        precondition(selectionBegin.clickContract == "v2")
        for version: Any in ["v3", "", NSNull(), 1, true] {
            var invalid = raw
            invalid["clickContract"] = version
            do {
                _ = try NativeBeginRequest(invalid)
                preconditionFailure("invalid version accepted")
            } catch { precondition((error as? DriverError)?.code == .invalidRequest) }
        }
        for kind in ["key", "read", "action"] {
            var invalid = extended
            invalid["mode"] = kind == "read" ? "read" : "write"
            invalid["intent"] = ["kind": kind, "parameters": [:]] as [String: Any]
            do {
                _ = try NativeBeginRequest(invalid)
                preconditionFailure("non-click negotiated v1")
            } catch { precondition((error as? DriverError)?.code == .invalidRequest) }
        }
        let op = try operation()
        defer { _ = op.finish() }
        let selection = NativeSelectionBasis(access: NativeApplicationAccess())
        let query = NativeInputQueryScope(
            binding: (target, [], "original-node"), desktop: false, selection: selection, scope: "surface")
        func declarations(_ previous: [NativePrimitiveContract]) -> NativePrimitiveDeclarations {
            NativePrimitiveDeclarations(
                previous: previous, op: op, query: query, within: [], actuator: nil, receiver: nil, basis: [])
        }
        let first = declarations([])
        let pid = NativeClickContract(
            routeFamily: "macos.windowDirected.v1", delivery: .windowDirected, admission: .preparationRequired,
            reason: nil, preparationOptions: ["background"])
        let hid = NativeClickContract(
            routeFamily: "macos.hid.v1", delivery: .systemPointer, admission: .preparationRequired, reason: nil,
            preparationOptions: ["foreground"], backgroundFamily: pid.routeFamily)
        for offer in [pid, hid] { first.declareClick(.pointer(.click), offer: offer) }
        precondition(first.current.count == 2 && first.current[0].id != first.current[1].id)
        precondition(first.current.map { $0.click!.delivery } == [.windowDirected, .systemPointer])
        for primitive in first.primitives {
            let click = primitive["click"] as! [String: Any]
            precondition(
                Set(click.keys) == [
                    "routeFamily", "interaction", "delivery", "admission", "reason", "preparationOptions",
                    "preferenceProfile"
                ])
            precondition(click["interaction"] as? String == "pointer")
            precondition(click["reason"] is NSNull)
            precondition(Set(click["preparationOptions"] as! [String]).count == 1)
        }
        let refreshed = declarations(first.current)
        refreshed.declareClick(.pointer(.click), offer: hid)
        refreshed.declareClick(.pointer(.click), offer: pid)
        precondition(
            refreshed.current.map(\.id) == first.current.reversed().map(\.id), "IDs cannot conflate deliveries")
        let legacy = NativeExecutionReceipt(target: target, onEffect: {})
        precondition(legacy.wire["click"] == nil)
        precondition(
            NativeInputUnit.pointer(.click).declaration(
                id: "legacy", supported: true, readiness: NativeReadiness(), exclusive: false)["click"] == nil)
    }

    @MainActor static func rowSelectionEligibility() {
        precondition(NativeRowSelection.accepts(role: "AXRow", actions: [], selected: false))
        for role in ["AXButton", "AXCell", "AXTextField", "AXOutline", "AXImage"] {
            precondition(!NativeRowSelection.accepts(role: role, actions: [], selected: false))
        }
        precondition(!NativeRowSelection.accepts(role: "AXRow", actions: ["AXPress"], selected: false))
        precondition(!NativeRowSelection.accepts(role: "AXRow", actions: nil, selected: false))
        precondition(!NativeRowSelection.accepts(role: "AXRow", actions: [], selected: true))
        precondition(!NativeRowSelection.accepts(role: "AXRow", actions: [], selected: nil))
    }

    @MainActor static func documentContextBoundary() {
        for prepared in [false, true] {
            precondition(NativeDocumentContext.permitsDispatch(.target, prepared: prepared))
            precondition(
                !NativeDocumentContext.permitsDispatch(.other, prepared: prepared),
                "a known sibling document must never receive the original button's action")
        }
        precondition(
            NativeDocumentContext.permitsDispatch(.unavailable, prepared: false),
            "missing AX focus cannot become a global denial for independent actions")
        precondition(
            !NativeDocumentContext.permitsDispatch(.unavailable, prepared: true),
            "lost readback cannot replace the exact context required by preparation")
    }

    @MainActor static func preparationObservation() throws {
        for (active, end, expected): (Bool?, NativeFocusOutcome.WaitEnd, String) in [
            (true, .activeObserved, "satisfied"), (false, .exhausted, "exhausted"),
            (nil, .providerUnknown, "unavailable")
        ] {
            let evidence = NativeClickEvidence(target: target, family: "macos.windowDirected.v1")
            evidence.record(.init(kind: .focusNotification, phase: .willSend, detail: "focus notification", window: 7))
            evidence.record(.init(kind: .focusNotification, phase: .returned, detail: "focus notification", window: 7))
            let state = NativeFocusSnapshot(
                foreground: false, reportedActive: active, focusedWindow: nil, prepared: true)
            evidence.observe(.init(initial: state, final: state, waitEnd: end))
            let value = evidence.wire
            let prep = (value["preparationEvidence"] as! [[String: Any]])[0]
            precondition(prep["dispatch"] as? String == "dispatched" && prep["wait"] as? String == expected)
            precondition((prep["observations"] as! [String]).contains("systemForeground=false"))
            precondition(evidence.permitsReselection, "an observed active bit must not become a global click gate")
            let transport = value["transportEvidence"] as! [String: Any]
            precondition(transport["kind"] as? String == "none" && transport["callEntered"] as? Bool == false)
            precondition(transport["submitted"] is NSNull && transport["expected"] is NSNull)
        }
    }

    @MainActor static func preparationContinuation() async throws {
        let offer = NativeClickContract(
            routeFamily: "macos.windowDirected.v1", delivery: .windowDirected, admission: .preparationRequired,
            reason: nil, preparationOptions: ["background"])
        for (kind, returned, cleanup, expected): (NativePreparationEffect.Kind, Bool, Bool, String) in [
            (.focusNotification, true, true, "reselectWithinScope"), (.focusNotification, false, true, "observe"),
            (.menuContext, true, true, "observe"), (.menuContext, false, true, "observe"),
            (.viewport, true, true, "observe"), (.viewport, false, true, "observe"),
            (.windowSelection, true, false, "stop")
        ] {
            let op = try operation()
            defer { _ = op.finish() }
            let evidence = NativeClickEvidence(target: target, family: offer.routeFamily)
            let receipt = NativeExecutionReceipt(target: target, onEffect: {})
            receipt.click = evidence
            evidence.record(.init(kind: kind, phase: .willSend, detail: "probe", window: 7))
            if returned { evidence.record(.init(kind: kind, phase: .returned, detail: "probe", window: 7)) }
            if !cleanup { receipt.recordCleanupUnknown() }
            BuildNativeClickFailurePass.run(
                NativePreparationUnavailable(description: "unavailable"), command: try command(), operation: op,
                step: 1, offer: offer, within: [], receipt: receipt, evidence: evidence)
            let continuation = evidence.wire["continuation"] as! [String: Any]
            precondition(continuation["kind"] as? String == expected)
            precondition(NSDictionary(dictionary: continuation["target"] as! [String: Any]).isEqual(to: target))
            precondition(receipt.dispatch == "notDispatched" && !op.inputStarted)
            if expected == "reselectWithinScope" {
                precondition(receipt.retryReason == "preparationUnavailable")
                try op.requireClickForeground(
                    target: target, within: [], backgroundFamily: offer.routeFamily, backgroundUnavailable: false,
                    hostAllowed: true, exclusive: false)
            } else {
                precondition(receipt.retryReason == nil)
                var executed = false
                do {
                    _ = try await op.execute(step: 2, payload: Data("second".utf8)) {
                        executed = true
                        return [:]
                    }
                    preconditionFailure("incompatible preparation allowed another native attempt")
                } catch { precondition((error as? DriverError)?.code == .executionUnknown && !executed) }
            }
        }
        // A completed menu-context preparation may continue only its original
        // candidate, after scope/basis checks; failure never authorizes reselect.
        let complete = NativeClickEvidence(target: target, family: offer.routeFamily)
        complete.record(.init(kind: .menuContext, phase: .willSend, detail: "probe", window: 7))
        complete.record(.init(kind: .menuContext, phase: .returned, detail: "probe", window: 7))
        complete.recordWait(.satisfied)
        complete.complete()
        let item = (complete.wire["preparationEvidence"] as! [[String: Any]])[0]
        precondition(
            item["effect"] as? String == "contentPossible" && item["continuation"] as? String == "sameCandidate")
        precondition(item["wait"] as? String == "satisfied")
        let op = try operation()
        defer { _ = op.finish() }
        let changed = NativeExecutionReceipt(target: target, onEffect: {})
        changed.click = NativeClickEvidence(target: target, family: offer.routeFamily)
        BuildNativeClickFailurePass.run(
            NativeSelectionBasis.Changed(), command: try command(), operation: op, step: 1, offer: offer, within: [],
            receipt: changed, evidence: changed.click!)
        precondition(
            (changed.click!.wire["continuation"] as! [String: Any])["kind"] as? String == "sameCandidateRefresh")
    }

    @MainActor static func foregroundAuthority() throws {
        let op = try operation()
        defer { _ = op.finish() }
        let family = "macos.windowDirected.v1"
        func require(
            target: [String: Any] = NativeClickTests.target, within: [String] = ["parent"],
            family: String = "macos.windowDirected.v1", unavailable: Bool = false, host: Bool = true,
            exclusive: Bool = false
        ) throws {
            try op.requireClickForeground(
                target: target, within: within, backgroundFamily: family, backgroundUnavailable: unavailable,
                hostAllowed: host, exclusive: exclusive)
        }
        do {
            try require()
            preconditionFailure()
        } catch { precondition((error as? DriverError)?.code == .invalidRequest) }
        try op.recordClickBackgroundUnavailable(family: family, target: target, within: ["parent"], step: 1)
        try require()  // Stable route/scope evidence has no dependency on an opaque ID.
        for mismatch in ["node", "surface", "rootWindow", "instance"] {
            var other = target
            other[mismatch] = "other"
            do {
                try require(target: other)
                preconditionFailure()
            } catch { precondition((error as? DriverError)?.code == .invalidRequest) }
        }
        do {
            try require(within: [])
            preconditionFailure()
        } catch { precondition((error as? DriverError)?.code == .invalidRequest) }
        do {
            try require(family: "different")
            preconditionFailure()
        } catch { precondition((error as? DriverError)?.code == .invalidRequest) }
        do {
            try require(host: false)
            preconditionFailure()
        } catch { precondition((error as? DriverError)?.code == .modeUnsupported) }
        try require(unavailable: true)
        try require(family: "different", exclusive: true)
        let fresh = try operation()
        defer { _ = fresh.finish() }
        do {
            try fresh.requireClickForeground(
                target: target, within: ["parent"], backgroundFamily: family, backgroundUnavailable: false,
                hostAllowed: true, exclusive: false)
            preconditionFailure("authority leaked to another operation")
        } catch { precondition((error as? DriverError)?.code == .invalidRequest) }
        op.recordInputDispatch()
        do {
            try require()
            preconditionFailure()
        } catch { precondition((error as? DriverError)?.code == .executionUnknown) }
    }

    @MainActor static func selectedActionPipeline() async throws {
        for measured in [false, true] {
            for selection in [false, true] {
                for mode in ClickProbeObjects.Mode.allCases {
                    let state = NativeDesktopState(), objects = ClickProbeObjects(mode: mode),
                        contracts = ClickProbeContracts()
                    let directory = NativeDirectory(state: state, session: "click-probe")
                    let scopes = NativeScopeResolver(
                        session: "click-probe", applications: state.applications, appSelections: state.appSelections,
                        sharing: state.sharing, desktop: { state.desktop }, directory: directory,
                        captures: NativeCapture(directory: directory, sharing: state.sharing), pointer: state.pointer)
                    let access = NativeControlAccess(
                        scopes: scopes, units: scopes, captures: scopes.captures, desktop: { state.desktop },
                        objects: objects, sharing: state.sharing, contracts: contracts, recordEffect: {})
                    let op = try operation(version: selection ? "v2" : "v1")
                    defer { _ = op.finish() }
                    let selected = try command(kind: selection ? "native_selection" : "native_action")
                    let row = NativeRowSelection.Contract(
                        owner: AXUIElementCreateApplication(getpid()), method: .replaceRows)
                    let offer = NativeClickContract(
                        routeFamily: selection ? row.family : "macos.axAction.AXPress",
                        delivery: selection ? .objectSelection : .objectAction, admission: .ready, reason: nil,
                        preparationOptions: [])
                    let contract = NativePrimitiveContract(
                        id: selected.primitiveID, node: "original-node", within: [], actuator: nil, receiver: nil,
                        basis: [], unit: selection ? .selection(row) : .action("AXPress", "old localized label"),
                        click: offer)
                    let resolved = ResolvedNativeTarget(contract: contract, binding: (target, [], "original-node"))
                    var marks = 0, checks = [String](), runs = 0
                    func execute() async throws -> [String: Any] {
                        try await op.execute(step: 1, payload: Data("fixed action command".utf8)) {
                            runs += 1
                            let receipt = NativeExecutionReceipt(target: target, onEffect: {})
                            receipt.click = NativeClickEvidence(target: target)
                            let boundary = NativeUnitBoundary(
                                checkConditions: { checks.append("scope") }, checkSelection: { checks.append("basis") },
                                markDispatchInFlight: { preconditionFailure("wrong delivery") },
                                checkKeyboardSelection: {
                                    preconditionFailure("click must not select a scoped keyboard route")
                                },
                                markPreparedKeyboardDispatchInFlight: {
                                    preconditionFailure("click is not scoped keyboard input")
                                },
                                beforeDispatch: {
                                    checks.append("dispatch")
                                    marks += 1
                                    receipt.dispatch = "unknown"
                                    op.recordInputDispatch()
                                })
                            await NativeClickPipeline.run(
                                selected, resolved: resolved, operation: op, step: 1, boundary: boundary,
                                receipt: receipt, access: access)
                            return receipt.wire
                        }
                    }
                    let metrics = measured ? NativeMetrics() : nil
                    let result = try await RequestDiagnostics.$metrics.withValue(metrics) { try await execute() }
                    let replay = try await execute()
                    if let metrics {
                        let report = metrics.finish(method: "execute", status: mode == .success ? "ok" : "error")!
                        let phases = report["phases"] as! [String: [String: Any]]
                        precondition(
                            Set(phases.keys) == [
                                "clickPlan", "clickPreparation", "clickTargetValidation", "clickDispatch"
                            ])
                        precondition(phases["clickPlan"]?["count"] as? Int == 1)
                        precondition(
                            phases["clickDispatch"]?["count"] as? Int == 1,
                            "even a failed dispatch phase must close its metric without changing the action count")
                        precondition(phases.values.allSatisfy { ($0["durationMs"] as? Double ?? -1) >= 0 })
                    }
                    precondition(runs == 1 && NSDictionary(dictionary: result).isEqual(to: replay))
                    let click = result["click"] as! [String: Any]
                    precondition(
                        (click["continuation"] as! [String: Any])["routeFamily"] as? String == offer.routeFamily)
                    precondition((click["preparationEvidence"] as! [[String: Any]]).isEmpty)
                    let transport = click["transportEvidence"] as! [String: Any]
                    if mode == .beforeCall {
                        precondition(
                            result["dispatch"] as? String == "notDispatched" && marks == 0 && objects.sends == 0)
                        precondition(
                            transport["kind"] as? String == "none" && transport["callEntered"] as? Bool == false)
                        precondition(result["retryReason"] as? String == "routeUnavailable")
                    } else {
                        precondition(marks == 1 && objects.sends == 1 && op.inputStarted)
                        precondition(transport["kind"] as? String == (selection ? "nativeSelection" : "nativeAction"))
                        precondition(
                            transport["callEntered"] as? Bool == true && transport["callReturned"] as? Bool == true)
                        precondition(result["dispatch"] as? String == (mode == .success ? "dispatched" : "unknown"))
                        precondition(result["retryReason"] is NSNull)
                        if mode == .success {
                            precondition(result["confirmation"] as? String == (selection ? "confirmed" : "unavailable"))
                        }
                        precondition(checks.firstIndex(of: "basis")! < checks.firstIndex(of: "dispatch")!)
                        do {
                            _ = try await op.execute(step: 2, payload: Data("attempt other delivery".utf8)) {
                                preconditionFailure("replayed")
                            }
                            preconditionFailure("second input admitted")
                        } catch { precondition((error as? DriverError)?.code == .executionUnknown) }
                    }
                }
            }
        }
    }
    @MainActor static func toggleEffectPipeline() async throws {
        precondition(NativeTogglePress.value(NSNumber(value: 1.5)) == nil)
        precondition(NativeTogglePress.value("unknown") == nil)
        precondition(!NativeTogglePress.canObserve(action: "AXPick", role: "AXCheckBox", before: "0"))
        for scenario in [
            "stable", "unchanged", "transient", "radioOff", "missing", "timeout", "cancelled", "expiredDuringRead"
        ] {
            let state = NativeDesktopState(), objects = ClickProbeObjects(mode: .returnedError)
            let directory = NativeDirectory(state: state, session: "toggle-probe")
            let scopes = NativeScopeResolver(
                session: "toggle-probe", applications: state.applications, appSelections: state.appSelections,
                sharing: state.sharing, desktop: { state.desktop }, directory: directory,
                captures: NativeCapture(directory: directory, sharing: state.sharing), pointer: state.pointer)
            let access = NativeControlAccess(
                scopes: scopes, units: scopes, captures: scopes.captures, desktop: { state.desktop }, objects: objects,
                sharing: state.sharing, contracts: ClickProbeContracts(), recordEffect: {})
            var reads = 0
            var expiredDuringRead = false
            objects.actionFailure = NativeTogglePress.Unconfirmed(
                role: scenario == "radioOff" ? "AXRadioButton" : "AXCheckBox",
                before: scenario == "radioOff" ? "1" : "0",
                status: scenario == "timeout" ? .cannotComplete : .actionUnsupported
            ) {
                reads += 1
                if scenario == "expiredDuringRead" && reads == 2 { expiredDuringRead = true }
                if scenario == "cancelled" { throw CancellationError() }
                if scenario == "missing" { return nil }
                if scenario == "unchanged" || scenario == "radioOff" || scenario == "transient" && reads > 1 {
                    return "0"
                }
                return "1"
            }
            let op = try operation()
            defer { _ = op.finish() }
            let selected = try command()
            let offer = NativeClickContract(
                routeFamily: "macos.axAction.AXPress", delivery: .objectAction, admission: .ready, reason: nil,
                preparationOptions: [])
            let contract = NativePrimitiveContract(
                id: selected.primitiveID, node: "original-node", within: [], actuator: nil, receiver: nil, basis: [],
                unit: .action("AXPress", nil), click: offer)
            let resolved = ResolvedNativeTarget(contract: contract, binding: (target, [], "original-node"))
            let receipt = NativeExecutionReceipt(target: target, onEffect: {})
            receipt.click = NativeClickEvidence(target: target)
            let boundary = NativeUnitBoundary(
                checkConditions: { if expiredDuringRead { throw RequestDeadlineExceeded() } }, checkSelection: {},
                markDispatchInFlight: {},
                checkKeyboardSelection: { preconditionFailure("click must not select a scoped keyboard route") },
                markPreparedKeyboardDispatchInFlight: { preconditionFailure("click is not scoped keyboard input") },
                beforeDispatch: {
                    receipt.dispatch = "unknown"
                    op.recordInputDispatch()
                })
            await NativeClickPipeline.run(
                selected, resolved: resolved, operation: op, step: 1, boundary: boundary, receipt: receipt,
                access: access)
            precondition(objects.sends == 1 && op.inputStarted, "effect observation must never replay the action")
            precondition(receipt.retryReason == nil)
            if scenario == "stable" {
                precondition(receipt.confirmation == "confirmed" && receipt.dispatch == "dispatched" && reads == 2)
                precondition(receipt.detail.contains("Application behavior is unconfirmed"))
            } else {
                precondition(
                    receipt.dispatch == "unknown" && !receipt.succeeded,
                    "unstable or unavailable evidence must retain failure")
            }
            precondition(
                (receipt.click!.wire["continuation"] as! [String: Any])["kind"] as? String != "reselectWithinScope")
        }
    }
}

@MainActor private final class ClickProbeContracts: NativeContractLookup {
    func primitiveContracts(_ token: String) -> [NativePrimitiveContract] { [] }
    func selectionBasis(_ token: String) -> NativeSelectionBasis { preconditionFailure("unused capability") }
}

@MainActor private final class ClickProbeObjects: NativeControlObjectAccess {
    func executeNativeSelection(
        _ ref: String, contract: NativeRowSelection.Contract, onDispatch: @MainActor () throws -> Void,
        onReturned: @MainActor () -> Void
    ) throws -> NativeElementControl.ActionResult {
        precondition(ref == "original-node" && contract.method == .replaceRows)
        if mode == .beforeCall { throw DriverError("selection changed", code: .inputUnavailable) }
        try onDispatch()
        sends += 1
        onReturned()
        if mode == .returnedError {
            throw DriverError("selection write failed; effect unknown", code: .executionUnknown, effect: .possible)
        }
        return .init(verified: true, detail: "Sole selected row confirmed; navigation unconfirmed.")
    }
    enum Mode: CaseIterable { case success, beforeCall, returnedError }
    let mode: Mode
    var sends = 0
    var actionFailure: Error?
    init(mode: Mode) { self.mode = mode }
    func retainNode(_ ref: String) -> (() -> Void)? { {} }
    func nativeActionFacts(_ ref: String) throws -> NativeElementControl.ActionFacts {
        precondition(ref == "original-node")
        return .init(role: "AXButton", actions: nil, enabled: true, applicationMenuContext: false, foreground: true)
    }
    func executeNativeAction(
        _ ref: String, action: String, description: String?, onDispatch: @MainActor () throws -> Void,
        onReturned: @MainActor () -> Void
    ) throws {
        precondition(ref == "original-node" && action == "AXPress")
        if mode == .beforeCall { throw DriverError("action vanished", code: .inputUnavailable) }
        try onDispatch()
        sends += 1
        onReturned()
        if let actionFailure { throw actionFailure }
        if mode == .returnedError {
            throw DriverError("AX cannotComplete; effect unknown", code: .executionUnknown, effect: .possible)
        }
    }
    func executeNativeTextValue(
        _ ref: String, contract: NativeElementControl.TextValueContract, text: String,
        onDispatch: @MainActor () throws -> Void
    ) throws -> NativeElementControl.ActionResult { preconditionFailure("unselected text branch") }
}
