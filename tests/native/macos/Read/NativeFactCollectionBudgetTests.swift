// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices

@main enum NativeFactCollectionBudgetTests {
    /// All clock reads remain on the test's main actor. The Sendable clock
    /// closure matches ObservationBudget without consulting a different clock.
    private final class Clock: @unchecked Sendable {
        var value = ProcessInfo.processInfo.systemUptime
    }

    @MainActor static func main() async throws {
        // A windowless process supplies only its own lifetime/PID. Every AX
        // getter below is injected; no surface is opened, activated or changed.
        NSApplication.shared.setActivationPolicy(.prohibited)
        for milliseconds in [10, 50, 150, 200] {
            try preservesSmallRoot(milliseconds)
            try preservesSmallRelation(milliseconds)
        }
        try expiredRequestDoesNotCollect()
        try await cancelledRequestDoesNotCollect()
        print("PASS short observation budgets collect real root/relation facts and preserve expired/cancelled boundaries")
    }

    @MainActor private static func root(_ registry: ApplicationElements) -> (String, AXUIElement) {
        let owner = NSRunningApplication.current
        let element = AXUIElementCreateApplication(owner.processIdentifier)
        let identity = ElementIdentity(facts: .init(values: ["AXRole": "AXButton"]))
        let target = ApplicationElements.Target(application: owner, launched: owner.launchDate,
            element: element, scope: "budget-window", role: "AXButton", identity: identity)
        let ref = registry.observe(.init(pid: owner.processIdentifier, launched: owner.launchDate,
            element: element, scope: target.scope), evidence: .init(identity: identity, provider: nil, context: []),
            value: target)
        return (ref, element)
    }

    @MainActor private static func preservesSmallRoot(_ milliseconds: Int) throws {
        let registry = ApplicationElements(), clock = Clock()
        let (ref, _) = root(registry)
        var batches = 0, singleFallbacks = 0, relations = 0
        var attributes = AXAttributeReader.API()
        attributes.batch = { _, fields in
            batches += 1
            clock.value += 0.001
            return (.success, fields.map { $0 == "AXRole" ? "AXButton" : "Small budget title" } as CFArray)
        }
        attributes.single = { _, _ in singleFallbacks += 1; return (.cannotComplete, nil) }
        var relationAPI = AXRelationReader.API()
        relationAPI.count = { _, _ in relations += 1; return (.success, 0) }
        relationAPI.single = { _, _ in relations += 1; return (.noValue, nil) }
        let collector = NativeFactCollector(registry: registry, inspection: .init(registry: registry),
            attributes: attributes, relations: relationAPI, clock: { clock.value })
        let deadline = clock.value + Double(milliseconds) / 1000
        let result = try RequestDiagnostics.$deadline.withValue(deadline) {
            try collector.collect(ref, session: "budget-session", fields: ["AXRole", "AXTitle"],
                maxNodes: 1, maxDepth: 0, actionDetail: .none)
        }
        precondition(batches == 1 && singleFallbacks == 0 && relations == NativeFactCollector.relations.count,
                     "a positive sub-200ms request must reach its real acquisition mechanism")
        let nodes = result["nodes"] as! [[String: Any]]
        let values = nodes[0]["values"] as! [String: Any]
        let failures = nodes[0]["fieldStatus"] as! [String: Any]
        precondition(nodes.count == 1 && values["AXRole"] as? String == "AXButton")
        precondition(values["AXTitle"] as? String == "Small budget title" && failures.isEmpty)
        precondition((result["stopReasons"] as! [String]).isEmpty,
                     "the old fixed reserve cannot manufacture a collection timeout")
        let encoded = NativeObservationEncoding.encode(result)
        let graph = encoded["nodes"] as! [[String: Any]]
        let properties = graph.first { $0["id"] as? String == ref }!["properties"] as! [[String: Any]]
        let title = properties.first { ($0["key"] as? [String]) == ["macos.ax", "AXTitle"] }!["value"] as! [String: Any]
        precondition(title["nativeType"] as? String == "CFString" && title["data"] as? String == "Small budget title",
                     "short acquisition preserves full native field evidence through publication")
        precondition(clock.value < deadline, "the collector cannot refill the original request budget")
    }

    @MainActor private static func preservesSmallRelation(_ milliseconds: Int) throws {
        let registry = ApplicationElements(), clock = Clock()
        let (ref, element) = root(registry)
        var counts = 0, ranges = 0
        var api = AXRelationReader.API()
        api.count = { _, _ in counts += 1; clock.value += 0.001; return (.success, 1) }
        api.range = { _, _, length in
            ranges += 1
            clock.value += 0.001
            precondition(length == 1)
            return (.success, [element] as CFArray)
        }
        api.single = { _, _ in preconditionFailure("successful array acquisition needs no fallback") }
        let collector = NativeFactCollector(registry: registry, inspection: .init(registry: registry),
            relations: api, clock: { clock.value })
        let deadline = clock.value + Double(milliseconds) / 1000
        let result = try RequestDiagnostics.$deadline.withValue(deadline) {
            try collector.relation(ref, session: "budget-session", property: "AXChildren", cursor: nil, limit: 1)
        }
        let relation = result["relation"] as! [String: Any]
        precondition(counts == 2 && ranges == 1,
                     "short explicit relation reads still acquire and check the provider's actual page")
        precondition(relation["targets"] as? [String] == [ref] && relation["total"] as? Int == 1)
        precondition(relation["state"] as? String == "complete" && clock.value < deadline)
    }

    @MainActor private static func expiredRequestDoesNotCollect() throws {
        let registry = ApplicationElements(), clock = Clock()
        let (ref, _) = root(registry)
        var calls = 0
        var attributes = AXAttributeReader.API()
        attributes.batch = { _, _ in calls += 1; return (.success, [] as CFArray) }
        var api = AXRelationReader.API()
        api.count = { _, _ in calls += 1; return (.success, 0) }
        let collector = NativeFactCollector(registry: registry, inspection: .init(registry: registry),
            attributes: attributes, relations: api, clock: { clock.value })
        try RequestDiagnostics.$deadline.withValue(clock.value - 1) {
            do {
                _ = try NativeQueryPipeline.collect(resolveQueryScope: { ref }, collectRequestedFacts: { ref in
                    try collector.collect(ref, session: "budget-session", fields: ["AXRole"],
                        maxNodes: 1, maxDepth: 0, actionDetail: .none)
                }, checkQueryBoundary: { _ in try RequestDiagnostics.checkCancellation() },
                registerObservedObjects: { _, _ in preconditionFailure("expired facts cannot be published") })
                preconditionFailure("an expired absolute request must still fail its final boundary")
            } catch is RequestDeadlineExceeded {}
            let relation = try collector.relation(ref, session: "budget-session", property: "AXChildren", cursor: nil,
                limit: 1)["relation"] as! [String: Any]
            precondition(relation["state"] as? String == "timeout")
        }
        precondition(calls == 0, "the proportional reserve does not revive an expired request")
    }

    @MainActor private static func cancelledRequestDoesNotCollect() async throws {
        let registry = ApplicationElements()
        let (ref, _) = root(registry)
        var calls = 0
        var attributes = AXAttributeReader.API()
        attributes.batch = { _, _ in calls += 1; return (.success, [] as CFArray) }
        let collector = NativeFactCollector(registry: registry, inspection: .init(registry: registry), attributes: attributes)
        let task = Task { @MainActor in
            _ = try collector.collect(ref, session: "budget-session", fields: ["AXRole"],
                maxNodes: 1, maxDepth: 0, actionDetail: .none)
        }
        task.cancel()
        do { _ = try await task.value; preconditionFailure("cancellation cannot produce an observation") }
        catch is CancellationError {}
        precondition(calls == 0)
    }
}
