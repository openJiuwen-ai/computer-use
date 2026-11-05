// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices

/// Exercises the production declaration/encoding owner, without allocating
/// windows or substituting a selector. Live actuator checks remain separate.
@main enum NativeScrollOwnerPermissionTests {
    @MainActor static func main() throws {
        try borrowedScrollOwner()
        try receiverAliasesKeepPermission()
        try unrelatedUnitsKeepPermission()
        try pendingReceiverIsNotMadeReady()
        print("PASS scroll actuator permission is independent of a disabled content owner")
    }

    @MainActor static func declaration(
        requested: String, relation: String?, unit: NativeInputUnit, enabled: Bool?,
        readiness: NativeReadiness = NativeReadiness(), supported: Bool = true,
        barIsRegion: Bool = false
    ) throws -> (raw: String, primitive: [String: Any], contract: NativePrimitiveContract) {
        let state = NativeDesktopState()
        let binding: [String: Any] = [
            "session": "s", "instance": "app", "rootWindow": "0", "surface": "window", "node": requested,
            "capture": NSNull()
        ]
        let intent = try NativeIntent(
            ["kind": "scroll", "parameters": ["direction": "down", "pages": 1]], mode: "write")
        let op = NativeOperation(
            timeoutMs: 5000, writable: true, intent: intent, target: binding, release: {}, cancelActive: {})
        defer { _ = op.finish() }
        let actuator: NativeInspectQuery.Actuator?
        switch relation {
        case "scrollPage":
            actuator = try .init([
                "node": "page", "relation": "scrollPage", "region": "area", "bar": barIsRegion ? "area" : "bar"
            ])
        case "scrollPointer":
            actuator = try .init(["node": "hit", "relation": "scrollPointer", "region": "area"])
        case "controlPart", "containedEditor":
            actuator = try .init(["node": "member", "relation": relation!])
        default: actuator = nil
        }
        let query = NativeInputQueryScope(
            binding: (binding, [], requested),
            selection: NativeSelectionBasis(access: state.applications), scope: "window")
        let plan = NativeInputContextPlan(
            query: query, operation: op, within: [], actuator: actuator, receiver: nil, basis: [])
        let declarations = NativePrimitiveDeclarations(
            previous: [], op: op, query: query, within: [], actuator: actuator, receiver: nil, basis: [])
        declarations.declare(unit, supported: supported, enabled: enabled, readiness: { _ in readiness })
        let sampled = NativeSampledInput(
            plan: plan, declarations: declarations, enabled: enabled, foreground: false, input: [:],
            nativeRole: "AXButton", actionsComplete: false, applicationMenu: false)
        let encoded = EncodeNativeInputContextPass.run(sampled)
        let contracts = try declarations.registeredContracts()
        precondition(contracts.count == 1 && contracts[0].node == requested && contracts[0].actuator == actuator)
        return (encoded["enabled"] as! String, declarations.primitives[0], contracts[0])
    }

    @MainActor static func borrowedScrollOwner() throws {
        for (relation, unit) in [
            ("scrollPage", NativeInputUnit.scroll), ("scrollPointer", NativeInputUnit.pointer(.scroll))
        ] {
            for enabled: Bool? in [false, true, nil] {
                let result = try declaration(requested: "content", relation: relation, unit: unit, enabled: enabled)
                precondition(result.raw == (enabled.map { $0 ? "yes" : "no" } ?? "unknown"),
                             "raw requested enabled must stay observable, including false")
                precondition(result.primitive["supported"] as? String == "yes"
                             && result.primitive["readiness"] as? String == "yes",
                             "disabled content must not disable its independently selected scroll actuator: \(relation)")
                precondition((result.primitive["pending"] as? [String] ?? []).isEmpty,
                             "requested enabled is not an extra scroll receiver condition")
                precondition(result.contract.unit == unit && result.contract.node == "content",
                             "permission changes cannot rebind the original requested element")
            }
        }
        let barRegion = try declaration(
            requested: "content", relation: "scrollPage", unit: .scroll, enabled: false, barIsRegion: true)
        precondition(barRegion.raw == "no" && barRegion.primitive["readiness"] as? String == "yes")
    }

    @MainActor static func receiverAliasesKeepPermission() throws {
        for requested in ["area", "bar", "page"] {
            let result = try declaration(requested: requested, relation: "scrollPage", unit: .scroll, enabled: false)
            precondition(result.raw == "no" && result.primitive["readiness"] as? String == "no")
            precondition((result.primitive["pending"] as? [String]) == ["enabled"],
                         "an original target that is itself a page receiver still needs permission")
        }
        for requested in ["area", "hit"] {
            let result = try declaration(
                requested: requested, relation: "scrollPointer", unit: .pointer(.scroll), enabled: false)
            precondition(result.primitive["readiness"] as? String == "no"
                         && (result.primitive["pending"] as? [String]) == ["enabled"],
                         "wheel region/hit aliases cannot borrow the content-owner exception")
        }
    }

    @MainActor static func unrelatedUnitsKeepPermission() throws {
        for relation: String? in [nil, "scrollPage", "scrollPointer", "controlPart", "containedEditor"] {
            for unit in [NativeInputUnit.action("AXPress", nil), .pointer(.click), .keyboard(.insert)] {
                let result = try declaration(requested: "content", relation: relation, unit: unit, enabled: false)
                precondition(result.primitive["readiness"] as? String == "no"
                             && (result.primitive["pending"] as? [String]) == ["enabled"],
                             "scroll ownership must not weaken click, keyboard or other actuator permissions")
            }
        }
        for (relation, unit) in [
            ("scrollPointer", NativeInputUnit.scroll), ("scrollPage", NativeInputUnit.pointer(.scroll)),
            ("controlPart", NativeInputUnit.scroll), ("containedEditor", NativeInputUnit.pointer(.scroll))
        ] {
            let result = try declaration(requested: "content", relation: relation, unit: unit, enabled: false)
            precondition((result.primitive["pending"] as? [String]) == ["enabled"],
                         "only the exact scroll unit/actuator pair owns the exception")
        }
    }

    @MainActor static func pendingReceiverIsNotMadeReady() throws {
        for (relation, unit) in [
            ("scrollPage", NativeInputUnit.scroll), ("scrollPointer", NativeInputUnit.pointer(.scroll))
        ] {
            for pending in ["receiver", "foreground", "geometry", "enabled"] {
                let readiness = NativeReadiness([(pending, "no")], preparationAllowed: false)
                let result = try declaration(
                    requested: "content", relation: relation, unit: unit, enabled: false, readiness: readiness)
                precondition(result.raw == "no" && result.primitive["readiness"] as? String == "no")
                precondition((result.primitive["pending"] as? [String]) == [pending],
                             "the selected actuator's own failed condition cannot be removed or replaced")
                precondition(result.primitive["supported"] as? String == "no")
            }
            let unavailable = try declaration(
                requested: "content", relation: relation, unit: unit, enabled: false, supported: false)
            precondition(unavailable.primitive["supported"] as? String == "no",
                         "ignoring an unrelated owner state cannot invent scroll support")
        }
    }
}
