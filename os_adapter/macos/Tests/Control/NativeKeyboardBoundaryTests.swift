import Foundation

/// Execute production policy, parent Gate, alias registry and readiness error
/// propagation. No NSApplication, AX adapter, event or desktop query is invoked.
@main enum NativeKeyboardBoundaryTests {
    typealias Projection = NativeKeyboardBoundaryProjection<String>
    enum Stop: Error { case failed }

    @MainActor static func main() throws {
        eligibility()
        projectionDecisions()
        try actualParentGate()
        try throwingGateKeepsLegacy()
        knownAliasesAreReadOnly()
        try readinessPreservesIdentityFailures()
        print("NativeKeyboardBoundaryTests passed")
    }

    static func row(
        _ key: String, _ object: String, role: String = "AXWindow", modal: Bool? = false, parent: String? = nil,
        parentKnown: Bool = true
    ) -> Projection.Window {
        .init(key: key, element: object, role: role, modal: modal, parent: parent, parentKnown: parentKnown)
    }

    static func eligibility() {
        for missing in -1..<7 {
            var facts = [Bool](repeating: true, count: 7)
            if missing >= 0 { facts[missing] = false }
            precondition(
                NativeKeyboardBoundaryPolicy.eligible(
                    windowCommand: facts[0], unconstrained: facts[1], sameRoot: facts[2], exactNativeID: facts[3],
                    localProcess: facts[4], noObservedWeb: facts[5], sameKeyboardSurface: facts[6]) == (missing == -1))
        }
        // Legal attached text roots: original unit D, selected keyboard F.
        // Different actual recipient ID must select legacy, not a stale error.
        precondition(
            !NativeKeyboardBoundaryPolicy.eligible(
                windowCommand: true, unconstrained: true, sameRoot: true, exactNativeID: true, localProcess: true,
                noObservedWeb: true, sameKeyboardSurface: false))
        precondition(NativeKeyboardBoundaryPolicy(eligible: false).mode == .legacy)
    }

    static func projectionDecisions() {
        func decide(_ rows: [Projection.Window], issue: String? = nil) -> NativeKeyboardBoundaryPolicy.Decision {
            NativeKeyboardBoundaryPolicy.decide(
                original: "original-AX-D", projection: .init(windows: rows, issue: issue), same: { $0 == $1 })
        }
        for (parent, expected) in [
            (nil as String?, false), ("D", false), ("App", true), ("E", true), ("missing", false)
        ] {
            precondition(
                NativeKeyboardBoundaryPolicy.parentKnown(
                    child: "D", parent: parent, application: "App", windows: ["D", "E"], same: { $0 == $1 }) == expected
            )
        }
        let root = row("private:0", "original-AX-D")
        precondition(decide([root]) == .confirmed)
        // Unrelated unknown alias/native parent/optional modality remains a
        // real internal node; none of these fields authorizes replacement.
        let auxiliary = row("private:1", "new-aux-AX", modal: nil, parentKnown: false)
        precondition(decide([root, auxiliary]) == .confirmed)
        precondition(decide([root, row("private:1", "E", modal: true)]) != .confirmed)
        precondition(
            decide([root, row("private:1", "M", role: "AXSheet", modal: nil, parentKnown: false)]) != .confirmed)
        // The original explicit false remains false, including a sheet.
        precondition(decide([root, row("private:1", "M", role: "AXSheet", modal: false)]) == .confirmed)
        precondition(decide([row("private:0", "original-AX-D", parentKnown: false)]) != .confirmed)
        precondition(decide([row("private:0", "original-AX-D", parent: "E")]) != .confirmed)
        precondition(decide([row("private:0", "original-AX-D", role: "AXSheet")]) != .confirmed)
        precondition(decide([root], issue: "children budget") != .confirmed)
        precondition(decide([], issue: "AXWindows malformed") != .changed)
        precondition(decide([]) == .changed)
        // Same public-style key does not equate a replacement AX object.
        precondition(decide([row("private:0", "replacement-AX-D")]) == .changed)
        precondition(decide([root, row("private:1", "original-AX-D")]) != .confirmed)
    }

    @MainActor static func actualParentGate() throws {
        var policy = NativeKeyboardBoundaryPolicy(eligible: true)
        var trace = [String](), round = 0, results = [[String]]()
        for _ in 0..<4 {
            round += 1
            let result = try BuildNativeKeyboardRoutePass.sampleKeyboardBoundary(
                &policy,
                projection: {
                    trace.append("P\(round)")
                    return round == 1 ? .confirmed : .needsLegacy("new modal / full ownership")
                },
                legacy: {
                    trace.append("L\(round)")
                    return ["fresh:\(round)"]
                })
            results.append(result)
        }
        precondition(results == [[], ["fresh:2"], ["fresh:3"], ["fresh:4"]])
        precondition(trace == ["P1", "P2", "L2", "L3", "L4"])
        precondition(policy.mode == .legacy && policy.projectionHits == 1 && policy.legacySamples == 3)
        precondition(policy.firstNeedLegacy == "new modal / full ownership")
        policy.recordProjection(.confirmed)
        precondition(policy.mode == .legacy && policy.projectionHits == 1)
        var legacy = NativeKeyboardBoundaryPolicy(eligible: false)
        for _ in 0..<2 {
            _ = try BuildNativeKeyboardRoutePass.sampleKeyboardBoundary(
                &legacy, projection: { preconditionFailure("ineligible never projects") }, legacy: { [] })
        }
        precondition(legacy.legacySamples == 2 && legacy.projectionHits == 0)
        var changed = NativeKeyboardBoundaryPolicy(eligible: true)
        do {
            _ = try BuildNativeKeyboardRoutePass.sampleKeyboardBoundary(
                &changed, projection: { .changed },
                legacy: { preconditionFailure("identity conflict does not search another window") })
            preconditionFailure()
        } catch let error as DriverError { precondition(error.code == .staleTarget) }
    }

    @MainActor static func throwingGateKeepsLegacy() throws {
        for cancellation in [false, true] {
            var policy = NativeKeyboardBoundaryPolicy(eligible: true)
            do {
                _ = try BuildNativeKeyboardRoutePass.sampleKeyboardBoundary(
                    &policy, projection: { .needsLegacy("required parent unknown") },
                    legacy: {
                        if cancellation { throw CancellationError() }
                        throw Stop.failed
                    })
                preconditionFailure()
            } catch is CancellationError { precondition(cancellation) } catch Stop.failed {
                precondition(!cancellation)
            } catch { preconditionFailure("Gate changed original error: \(error)") }
            precondition(policy.mode == .legacy && policy.legacySamples == 1)
            _ = try BuildNativeKeyboardRoutePass.sampleKeyboardBoundary(
                &policy, projection: { preconditionFailure("throw cannot reset strategy") }, legacy: { [] })
            precondition(policy.legacySamples == 2 && policy.firstNeedLegacy == "required parent unknown")
        }
    }

    static func knownAliasesAreReadOnly() {
        let registry = ElementReferences<String>()
        precondition(registry.knownReference(for: "unknown") == nil)
        let original = registry.reference(for: "native:1", aliases: ["AX:D"])
        precondition(original == "0")
        precondition(registry.knownReference(for: "AX:D") == original)
        precondition(registry.knownReference(for: "aux") == nil)
        let changed = registry.reference(for: "native:2", aliases: ["AX:D"])
        precondition(changed == "1" && registry.knownReference(for: "AX:D") == changed)
        // Looking at the old owner neither restores the alias nor allocates.
        precondition(registry.knownReference(for: "native:1") == original)
        precondition(registry.knownReference(for: "AX:D") == changed)
        let reused = registry.reference(for: "native:1", aliases: ["AX:new-D"])
        precondition(reused == original)
        precondition(registry.knownReference(for: "AX:D") != registry.knownReference(for: "AX:new-D"))
        precondition(registry.allocate() == "2")
    }

    static func readinessPreservesIdentityFailures() throws {
        do {
            _ = try NativeReadiness.probeRoute(
                check: {}, probe: { throw DriverError("original mapping ID changed", code: .staleTarget) })
            preconditionFailure()
        } catch let error as DriverError { precondition(error.code == .staleTarget) }
        let unavailable = try NativeReadiness.probeRoute(
            check: {}, probe: { throw DriverError("mapping unavailable", code: .inputUnavailable) })
        precondition(!unavailable)
    }
}
