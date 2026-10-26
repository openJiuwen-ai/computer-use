import AppKit
import Foundation

/// Mechanism regressions for stage ordering and effect barriers. Native UI
/// adapters are not invoked; these execute the production pipeline functions.
@main struct NativePipelineContractTests {
    struct ProbeFailure: Error {}

    @MainActor static func main() async throws {
        NSApplication.shared.setActivationPolicy(.prohibited)
        try queryStages()
        try await preparationStages()
        try await dispatchStages()
        try failureReceipts()
        try await narrowRouteAndLocalExtension()
        print("PASS native query/prepare/dispatch stage order, effect barriers and retry evidence")
    }

    @MainActor static func queryStages() throws {
        for failure in -1...3 {
            var trace = [String]()
            func stage(_ index: Int, _ label: String) throws {
                trace.append(label)
                if failure == index { throw ProbeFailure() }
            }
            do {
                let output = try NativeQueryPipeline.collect(
                    resolveQueryScope: {
                        try stage(0, "resolve")
                        return "original-scope"
                    },
                    collectRequestedFacts: { scope in
                        precondition(scope == "original-scope")
                        try stage(1, "collect")
                        return ["node": "original-native-reference"]
                    },
                    checkQueryBoundary: { scope in
                        precondition(scope == "original-scope")
                        try stage(2, "boundary")
                    },
                    registerObservedObjects: { scope, facts in
                        precondition(scope == "original-scope" && facts["node"] == "original-native-reference")
                        try stage(3, "register")
                    })
                precondition(failure == -1 && output["node"] == "original-native-reference")
            } catch is ProbeFailure { precondition(failure >= 0) }
            let expected = ["resolve", "collect", "boundary", "register"]
            precondition(trace == Array(expected.prefix(failure < 0 ? 4 : failure + 1)))
        }
    }

    @MainActor static func operation() throws -> NativeOperation {
        NativeOperation(
            timeoutMs: 10_000, writable: true,
            intent: try NativeIntent(["kind": "insert", "parameters": ["text": "probe"]], mode: "write"), target: [:],
            release: {}, cancelActive: {})
    }

    @MainActor static func preparationStages() async throws {
        for changed in [false, true] {
            let op = try operation()
            defer { _ = op.finish() }
            let receipt = NativeExecutionReceipt(target: [:], onEffect: {})
            var trace = [String]()
            let boundary = NativeUnitBoundary(
                checkConditions: { preconditionFailure("the selected route owns condition-check timing") },
                checkSelection: {
                    trace.append("selection")
                    if changed { throw ProbeFailure() }
                }, markDispatchInFlight: { preconditionFailure("preparation is not requested input") },
                checkKeyboardSelection: {
                    preconditionFailure("generic preparation must not select a scoped keyboard route")
                },
                markPreparedKeyboardDispatchInFlight: {
                    preconditionFailure("preparation is not scoped keyboard input")
                }, beforeDispatch: { preconditionFailure("preparation is not requested input") })
            do {
                try await NativePreparePipeline.run(
                    {
                        trace.append("prepare")
                        receipt.recordPreparation("native element focus")
                        receipt.confirmation = "confirmed"
                    }, boundary: boundary, op: op, id: "selected-route")
                precondition(!changed)
            } catch is ProbeFailure { precondition(changed) }
            precondition(trace == ["prepare", "selection"])
            precondition(op.preparationConfirmed("selected-route") == !changed)
            precondition(!op.inputStarted && receipt.dispatch == "notDispatched")
            precondition(receipt.preparation == ["native element focus"])
        }
    }

    @MainActor static func dispatchStages() async throws {
        for alreadySatisfied in [false, true] {
            let receipt = NativeExecutionReceipt(target: [:], onEffect: {})
            var runs = 0
            try await NativeDispatchPipeline.run(
                {
                    runs += 1
                    return alreadySatisfied
                }, receipt: receipt)
            precondition(runs == 1)
            precondition(receipt.dispatch == (alreadySatisfied ? "notDispatched" : "dispatched"))
        }
        for inFlight in [false, true] {
            let receipt = NativeExecutionReceipt(target: [:], onEffect: {})
            var runs = 0
            do {
                try await NativeDispatchPipeline.run(
                    {
                        runs += 1
                        if inFlight { receipt.dispatch = "unknown" }
                        throw ProbeFailure()
                    }, receipt: receipt)
                preconditionFailure()
            } catch is ProbeFailure {}
            precondition(runs == 1 && receipt.dispatch == (inFlight ? "unknown" : "notDispatched"))
        }
        // The existing operation scope still deduplicates a completed pipeline
        // before any target/route query and rejects another requested input.
        let op = try operation()
        defer { _ = op.finish() }
        var sends = 0
        func execute() async throws -> [String: Any] {
            try await op.execute(step: 1, payload: Data("same-command".utf8)) {
                let receipt = NativeExecutionReceipt(target: [:], onEffect: {})
                do {
                    try await NativeDispatchPipeline.run(
                        {
                            receipt.dispatch = "unknown"
                            op.recordInputDispatch()
                            sends += 1
                            receipt.recordAccepted()
                            return false
                        }, receipt: receipt)
                } catch { receipt.recordFailure(error) }
                return receipt.wire
            }
        }
        let first = try await execute(), replay = try await execute()
        precondition(NSDictionary(dictionary: first).isEqual(to: replay) && sends == 1)
        do {
            _ = try await op.execute(step: 2, payload: Data("new-command".utf8)) {
                sends += 1
                return [:]
            }
            preconditionFailure()
        } catch { precondition((error as? DriverError)?.code == .executionUnknown && sends == 1) }
    }

    @MainActor static func failureReceipts() throws {
        for (kind, expected) in [("key_sequence", "preconditionChanged"), ("native_action", "routeUnavailable")] {
            for dispatched in [false, true] {
                let op = try operation()
                defer { _ = op.finish() }
                let receipt = NativeExecutionReceipt(target: [:], onEffect: {})
                if dispatched { receipt.dispatch = "unknown" }
                BuildNativeFailureReceiptPass.run(
                    DriverError("route unavailable", code: .inputUnavailable), kind: kind, foreground: false,
                    id: "route", op: op, receipt: receipt)
                precondition(receipt.retryReason == (dispatched ? nil : expected))
            }
        }
        let op = try operation()
        defer { _ = op.finish() }
        let receipt = NativeExecutionReceipt(target: [:], onEffect: {})
        BuildNativeFailureReceiptPass.run(
            NativePreparationUnavailable(description: "background unavailable"), kind: "prepare_receiver",
            foreground: false, id: "route", op: op, receipt: receipt)
        precondition(receipt.retryReason == "preparationUnavailable" && !op.inputStarted)
        try op.requireForegroundPreparation("route", hostAllowed: true)
        do {
            try op.requireForegroundPreparation("another-route", hostAllowed: true)
            preconditionFailure()
        } catch { precondition((error as? DriverError)?.code == .invalidRequest) }
    }
}

/// A local route test borrows only the declared capabilities. It neither
/// constructs NativeExecution nor needs an application/window or desktop input.
@MainActor private final class ProbeContractAccess: NativeContractAccess {
    var values = [NativePrimitiveContract]()
    func primitiveContracts(_ token: String) -> [NativePrimitiveContract] { values }
    func replacePrimitiveContracts(_ token: String, _ values: [NativePrimitiveContract]) { self.values = values }
    func selectionBasis(_ token: String) -> NativeSelectionBasis { preconditionFailure("unused query capability") }
}

@MainActor private final class ProbeControlObjects: NativeControlObjectAccess {
    func executeNativeSelection(
        _ ref: String, contract: NativeRowSelection.Contract, onDispatch: @MainActor () throws -> Void,
        onReturned: @MainActor () -> Void
    ) throws -> NativeElementControl.ActionResult { preconditionFailure("unselected selection branch must not run") }
    var sends = 0
    var fail = false
    func retainNode(_ ref: String) -> (() -> Void)? { {} }
    func nativeActionFacts(_ ref: String) throws -> NativeElementControl.ActionFacts {
        preconditionFailure("action construction must not sample an unselected native route")
    }
    func executeNativeAction(
        _ ref: String, action: String, description: String?, onDispatch: @MainActor () throws -> Void,
        onReturned: @MainActor () -> Void
    ) throws {
        precondition(ref == "original-node" && action == "AXPress")
        try onDispatch()
        sends += 1
        onReturned()
        if fail { throw NativePipelineContractTests.ProbeFailure() }
    }
    func executeNativeTextValue(
        _ ref: String, contract: NativeElementControl.TextValueContract, text: String,
        onDispatch: @MainActor () throws -> Void
    ) throws -> NativeElementControl.ActionResult { preconditionFailure("unselected text branch must not run") }
}

extension NativePipelineContractTests {
    @MainActor static func narrowRouteAndLocalExtension() async throws {
        for failed in [false, true] {
            let state = NativeDesktopState(), objects = ProbeControlObjects(), contracts = ProbeContractAccess()
            let directory = NativeDirectory(state: state, session: "route-probe")
            let scopes = NativeScopeResolver(
                session: "route-probe", applications: state.applications, appSelections: state.appSelections,
                sharing: state.sharing, desktop: { state.desktop }, directory: directory,
                captures: NativeCapture(directory: directory, sharing: state.sharing), pointer: state.pointer)
            let access = NativeControlAccess(
                scopes: scopes, units: scopes, captures: scopes.captures, desktop: { state.desktop }, objects: objects,
                sharing: state.sharing, contracts: contracts, recordEffect: {})
            let op = NativeOperation(
                timeoutMs: 10000, writable: true,
                intent: try NativeIntent(
                    ["kind": "click", "parameters": ["button": "left", "count": 1]], mode: "write"), target: [:],
                release: {}, cancelActive: {})
            defer { _ = op.finish() }
            let command = try NativeUnitCommand([
                "kind": "native_action",
                "target": [
                    "session": "s", "instance": "i", "rootWindow": "w", "surface": "surface", "node": "original-node",
                    "capture": NSNull()
                ], "primitiveId": "p", "foreground": false
            ])
            let contract = NativePrimitiveContract(
                id: "p", node: "original-node", within: [], actuator: nil, receiver: nil, basis: [],
                unit: .action("AXPress", nil))
            let receipt = NativeExecutionReceipt(target: [:], onEffect: {})
            var marks = 0
            let boundary = NativeUnitBoundary(
                checkConditions: {}, checkSelection: {}, markDispatchInFlight: {},
                checkKeyboardSelection: {
                    preconditionFailure("object action must not select a scoped keyboard route")
                },
                markPreparedKeyboardDispatchInFlight: {
                    preconditionFailure("object action is not scoped keyboard input")
                },
                beforeDispatch: {
                    marks += 1
                    receipt.dispatch = "unknown"
                    op.recordInputDispatch()
                })
            let resolved = ResolvedNativeTarget(contract: contract, binding: ([:], [], "original-node"))
            let context = NativeRouteContext(
                command: command, operation: op, resolved: resolved, boundary: boundary, receipt: receipt,
                access: access)
            let route = try BuildNativeActionRoutePass.run(context, name: "AXPress", description: nil)
            precondition(objects.sends == 0 && marks == 0 && !op.inputStarted)
            objects.fail = failed
            guard case .dispatch(let send) = route else { preconditionFailure("wrong effect branch") }
            do {
                try await NativeDispatchPipeline.run(send, receipt: receipt)
                precondition(!failed)
            } catch is ProbeFailure { precondition(failed) }
            precondition(objects.sends == 1 && marks == 1 && op.inputStarted)
            precondition(receipt.dispatch == (failed ? "unknown" : "dispatched"))
        }
        // An unregistered extension can add a pure projection stage locally;
        // it reuses the production query composition without a new state owner.
        var trace = [String]()
        let output = try NativeQueryPipeline.collect(
            resolveQueryScope: { "scope" },
            collectRequestedFacts: { scope in
                trace.append(scope)
                return ProbeLocalFactExtension.apply(["node": "same-native-reference"])
            }, checkQueryBoundary: { trace.append($0) }, registerObservedObjects: { _, _ in trace.append("publish") })
        precondition(output["node"] == "same-native-reference" && output["local-probe"] == "yes")
        precondition(trace == ["scope", "scope", "publish"])
    }
}

private enum ProbeLocalFactExtension {
    static func apply(_ facts: [String: String]) -> [String: String] {
        var result = facts
        result["local-probe"] = "yes"
        return result
    }
}
