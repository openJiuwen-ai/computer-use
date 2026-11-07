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
        if CommandLine.arguments.contains("--relation-slots-wire") {
            let pages = try sparseRelationSlots()
            print(String(decoding: try JSONSerialization.data(withJSONObject: pages), as: UTF8.self))
            return
        }
        for milliseconds in [10, 50, 150, 200] {
            try preservesSmallRoot(milliseconds)
            try preservesSmallRelation(milliseconds)
        }
        try changedRootFactsCannotUseRetainedIdentity()
        try childIndividualFactsCannotUseRetainedIdentity(
            display: ["AXRole": "AXTextField"], fallback: .unavailable(.cannotComplete),
            expected: ["AXRole": "AXTextField"])
        try childIndividualFactsCannotUseRetainedIdentity(
            display: ["AXIdentifier": "new"], fallback: .value(.init(values: ["AXRole": "AXTextField"])),
            expected: ["AXRole": "AXTextField", "AXIdentifier": "new"])
        try childIndividualFactsCannotUseRetainedIdentity(
            display: ["AXRole": "AXButton"], fallback: .value(.init(values: ["AXIdentifier": "new"])),
            expected: ["AXRole": "AXButton", "AXIdentifier": "new"])
        try childIndividualFactsCannotUseRetainedIdentity(
            display: ["AXRole": "AXTextField"], fallback: .value(.init(values: ["AXRole": "AXButton"])),
            expected: nil)
        try observesScopedFocusWithoutInventingChildren()
        try observesScopedFocusWithoutInventingChildren(scopeRole: kAXSheetRole)
        _ = try sparseRelationSlots()
        try expiredRequestDoesNotCollect()
        try closeButtonUsesSingleObjectGetter()
        try await cancelledRequestDoesNotCollect()
        print("PASS short observation budgets collect real root/relation facts and preserve expired/cancelled boundaries")
    }

    @MainActor private static func sparseRelationSlots() throws -> [[String: Any]] {
        let registry = ApplicationElements()
        let (ref, window) = root(registry, role: "AXWindow")
        let pid = NSRunningApplication.current.processIdentifier
        let elements = (0..<5).map { AXUIElementCreateApplication(pid + 110_000 + pid_t($0)) }
        var order = elements
        func invalid(_ element: AXUIElement) -> Bool {
            CFEqual(element, elements[1]) || CFEqual(element, elements[3])
        }
        var attributes = AXAttributeReader.API()
        attributes.batch = { element, fields in
            if invalid(element) { return (.invalidUIElement, nil) }
            return (.success, fields.map { _ in CFEqual(element, window) ? "AXWindow" : "AXButton" } as CFArray)
        }
        var relations = AXRelationReader.API()
        let labelTargets = "AXServesAsTitleForUIElements"
        relations.count = { element, property in
            (.success, CFEqual(element, window) && [kAXChildrenAttribute, labelTargets].contains(property)
                ? order.count : 0)
        }
        relations.range = { _, _, length in (.success, Array(order.prefix(length)) as CFArray) }
        relations.single = { _, _ in (.noValue, nil) }
        let collector = NativeFactCollector(registry: registry, inspection: .init(registry: registry),
            attributes: attributes, relations: relations, readIdentity: { element in
                invalid(element) ? .invalid : .value(.init(values: ["AXRole": "AXButton"]))
            })
        let first = try collector.collect(ref, session: "budget-session", fields: ["AXRole"],
            maxNodes: 4, maxDepth: 1, actionDetail: .none)
        let links = first["relations"] as! [[String: Any]]
        let children = links.first { $0["source"] as? String == ref && $0["property"] as? String == kAXChildrenAttribute }!
        let acquired = children["targets"] as! [String]
        precondition(acquired.count == 2 && registry.target(acquired[1]).map { CFEqual($0.element, elements[2]) } == true,
            "a native invalid child must not hide readable siblings behind it")
        precondition(children["inspectedCount"] as? Int == 3 && children["unavailablePositions"] as? [Int] == [1]
            && children["state"] as? String == "partial")
        let deferred = links.first { $0["source"] as? String == ref && $0["property"] as? String == labelTargets }!
        let related = deferred["targets"] as! [String]
        precondition(related.count == 3 && deferred["inspectedCount"] as? Int == 5
            && deferred["unavailablePositions"] as? [Int] == [1, 3] && deferred["cursor"] == nil,
            "deferred metadata-only relations must preserve sparse native positions too")

        let cursor = children["cursor"] as! String
        let later = try collector.relation(ref, session: "budget-session", property: kAXChildrenAttribute,
            cursor: cursor, limit: 2)
        let suffix = later["relation"] as! [String: Any]
        let suffixTargets = suffix["targets"] as! [String]
        precondition(suffix["offset"] as? Int == 3 && suffix["inspectedCount"] as? Int == 2
            && suffix["unavailablePositions"] as? [Int] == [3] && suffix["cursor"] == nil
            && suffixTargets.count == 1 && registry.target(suffixTargets[0]).map {
                CFEqual($0.element, elements[4])
            } == true, "continuation must start after the original native slots, including gaps")

        let reader = AXRelationReader(api: relations)
        guard case .page(let page) = try reader.read(window, source: ref, property: kAXChildrenAttribute, limit: 4)
        else { preconditionFailure("expected the first relation page") }
        let stopped = reader.register(page) { element in
            if CFEqual(element, elements[2]) { return .stop }
            return invalid(element) ? .unavailable : .target(acquired[0])
        }
        let afterStop = reader.encode(page, registration: stopped)["cursor"] as! String
        guard case .page(let resumed) = try reader.read(
            window, source: ref, property: kAXChildrenAttribute, limit: 2, cursor: afterStop)
        else { preconditionFailure("expected the continued relation page") }
        precondition(resumed.offset == 2 && CFEqual(resumed.targets.first!, elements[2]),
            "budget stop must not consume the first unresolved slot")
        let blocked = reader.encode(resumed, registration: reader.register(resumed) { _ in .stop })
        precondition(blocked["cursor"] == nil && blocked["inspectedCount"] == nil,
            "a zero-progress continuation must not mint a new cursor")

        order.swapAt(1, 2)
        do {
            _ = try collector.relation(ref, session: "budget-session", property: kAXChildrenAttribute,
                cursor: cursor, limit: 2)
            preconditionFailure("a missing slot's original native identity must anchor the cursor")
        } catch let error as DriverError { precondition(error.code == .snapshotExpired) }
        return [NativeObservationEncoding.encode(first), NativeObservationEncoding.encode(later)]
    }

    @MainActor private static func root(_ registry: ApplicationElements, role: String = "AXButton")
        -> (String, AXUIElement)
    {
        let owner = NSRunningApplication.current
        let element = AXUIElementCreateApplication(owner.processIdentifier)
        let identity = ElementIdentity(facts: .init(values: ["AXRole": role]))
        let target = ApplicationElements.Target(application: owner, launched: owner.launchDate,
            element: element, scope: "budget-window", role: role, identity: identity)
        let ref = registry.observe(.init(pid: owner.processIdentifier, launched: owner.launchDate,
            element: element, scope: target.scope), evidence: .init(identity: identity, provider: nil, context: []),
            value: target)
        return (ref, element)
    }

    @MainActor private static func observesScopedFocusWithoutInventingChildren(
        scopeRole: String = kAXWindowRole
    ) throws {
        let registry = ApplicationElements()
        let (ref, window) = root(registry, role: scopeRole)
        let pid = NSRunningApplication.current.processIdentifier
        let focus = AXUIElementCreateApplication(pid + 100_000)
        let otherWindow = AXUIElementCreateApplication(pid + 100_001)
        let outerWindow = AXUIElementCreateApplication(pid + 100_003)
        var owner = window
        var focusParent = window
        var focusedReads = 0
        var enclosingWindowReads = 0
        var attributes = AXAttributeReader.API()
        attributes.batch = { element, fields in
            let role = CFEqual(element, focus) ? kAXTextFieldRole : scopeRole
            return (.success, fields.map { _ in role } as CFArray)
        }
        attributes.single = { element, field in
            switch field {
            case kAXFocusedUIElementAttribute:
                focusedReads += 1
                return (.success, focus)
            case kAXParentAttribute where CFEqual(element, focus):
                return (.success, focusParent)
            case kAXWindowAttribute where CFEqual(element, focus):
                return (.success, owner)
            case kAXWindowAttribute where scopeRole == kAXSheetRole && CFEqual(element, window):
                enclosingWindowReads += 1
                return (.success, outerWindow)
            default:
                return (.noValue, nil)
            }
        }
        var relations = AXRelationReader.API()
        var ordinaryChild: AXUIElement?
        relations.count = { element, property in
            (.success, CFEqual(element, window) && property == kAXChildrenAttribute && ordinaryChild != nil ? 1 : 0)
        }
        relations.range = { _, _, _ in (.success, ordinaryChild.map { [$0] as CFArray }) }
        relations.single = { _, _ in (.noValue, nil) }
        let collector = NativeFactCollector(registry: registry, inspection: .init(registry: registry),
            attributes: attributes, relations: relations)
        func read(focused: Bool, maxNodes: Int = 3) throws -> [String: Any] {
            try collector.collect(ref, session: "budget-session", fields: ["AXRole"], maxNodes: maxNodes, maxDepth: 2,
                followRelations: ["AXVisibleRows"], actionDetail: .none, focusedElement: focused)
        }
        let ordinary = try read(focused: false)
        precondition((ordinary["nodes"] as! [[String: Any]]).count == 1 && focusedReads == 0)
        let captured = try read(focused: true)
        let nodes = captured["nodes"] as! [[String: Any]]
        precondition(nodes.count == 2 && focusedReads == 2,
            "the scoped focused element omitted from Children must be sampled once")
        precondition(enclosingWindowReads == 0, "the exact selected sheet needs no enclosing-window lookup")
        let links = captured["relations"] as! [[String: Any]]
        let children = links.first {
            $0["source"] as? String == ref && $0["property"] as? String == kAXChildrenAttribute
        }
        precondition(children?["targets"] as? [String] == [], "focus is not a fabricated child")
        let focusRef = nodes[1]["node"] as! String
        let parent = links.first {
            $0["source"] as? String == focusRef && $0["property"] as? String == kAXParentAttribute
        }
        precondition(parent?["targets"] as? [String] == [ref], "the actual native Parent relation survives")
        let focusLinks = links.filter { $0["source"] as? String == focusRef }
        precondition(Set(focusLinks.compactMap { $0["property"] as? String })
            == Set(captured["requestedRelations"] as! [String]),
            "the wire protocol requires a result for every requested relationship on a sampled node")
        let focusChildren = focusLinks.first { $0["property"] as? String == kAXChildrenAttribute }
        precondition(focusChildren?["state"] as? String == "partial" && focusChildren?["certainty"] as? String == "unknown"
            && focusChildren?["targets"] as? [String] == [] && focusChildren?["total"] == nil)
        let encoded = NativeObservationEncoding.encode(captured)
        let graph = encoded["nodes"] as! [[String: Any]]
        let properties = graph.first { $0["id"] as? String == ref }!["properties"] as! [[String: Any]]
        let seed = properties.first { $0["key"] as? [String] == ["toolkit", "focused_read_seed"] }!
        precondition((seed["value"] as? [String: Any])?["nativeType"] as? String == "AXUIElement")
        if scopeRole == kAXSheetRole {
            owner = outerWindow
            let ownedByEnclosingWindow = try read(focused: true)
            precondition((ownedByEnclosingWindow["nodes"] as! [[String: Any]]).count == 2,
                "the enclosing window may also own focus whose Parent path reaches the selected sheet")
            focusParent = otherWindow
            let sibling = try read(focused: true)
            precondition((sibling["nodes"] as! [[String: Any]]).count == 1,
                "sharing the enclosing window cannot borrow focus from a sibling sheet")
            focusParent = window
        }
        owner = otherWindow
        let outside = try read(focused: true)
        precondition((outside["nodes"] as! [[String: Any]]).count == 1,
            "an actual window contradiction cannot expand the selected root")
        let outsideGraph = NativeObservationEncoding.encode(outside)["nodes"] as! [[String: Any]]
        let outsideProperties = outsideGraph[0]["properties"] as! [[String: Any]]
        let outsideSeed = outsideProperties.first { $0["key"] as? [String] == ["toolkit", "focused_read_seed"] }!
        precondition((outsideSeed["value"] as? [String: Any])?["nativeType"] as? String == "CFNull")

        owner = window
        ordinaryChild = AXUIElementCreateApplication(pid + 100_002)
        let capped = try read(focused: true, maxNodes: 2)
        let cappedNodes = capped["nodes"] as! [[String: Any]]
        precondition(cappedNodes.count == 2 && cappedNodes[1]["node"] as? String == focusRef,
            "an omitted focused editor must get one slot even when ordinary traversal would fill the node budget")
        let cappedLinks = capped["relations"] as! [[String: Any]]
        let cappedChildren = cappedLinks.first {
            $0["source"] as? String == ref && $0["property"] as? String == kAXChildrenAttribute
        }
        precondition(cappedChildren?["targets"] as? [String] == [] && cappedChildren?["total"] as? Int == 1,
            "the focus slot must not replace a real child in the Children relation")

        ordinaryChild = focus
        let contained = try read(focused: true, maxNodes: 2)
        let containedLinks = contained["relations"] as! [[String: Any]]
        let containedChildren = containedLinks.first {
            $0["source"] as? String == ref && $0["property"] as? String == kAXChildrenAttribute
        }
        precondition(containedChildren?["targets"] as? [String] == [focusRef]
            && containedChildren?["state"] as? String == "complete",
            "an ordinary path reuses the focused sample and preserves actual child coverage")
    }

    @MainActor private static func changedRootFactsCannotUseRetainedIdentity() throws {
        let registry = ApplicationElements()
        let (ref, _) = root(registry)
        var attributes = AXAttributeReader.API()
        attributes.batch = { _, fields in
            (.success, fields.map { $0 == "AXRole" ? "AXTextField" : "New object" } as CFArray)
        }
        let collector = NativeFactCollector(registry: registry, inspection: .init(registry: registry),
            attributes: attributes)
        do {
            _ = try collector.collect(ref, session: "budget-session", fields: ["AXRole", "AXTitle"],
                maxNodes: 1, maxDepth: 0, actionDetail: .none)
            throw DriverError("replacement facts were published under the original button's reference")
        } catch let error as DriverError where error.code == .staleTarget {}
        precondition(registry.target(ref) == nil)
    }

    @MainActor private static func childIndividualFactsCannotUseRetainedIdentity(
        display: [String: String], fallback: AXRead<ElementIdentity.Facts>, expected: [String: String]?
    ) throws {
        let registry = ApplicationElements()
        let (ref, window) = root(registry, role: "AXWindow")
        let parent = registry.target(ref)!
        let app = parent.application
        let child = AXUIElementCreateApplication(app.processIdentifier + 100_000)
        let identity = ElementIdentity(facts: .init(values: ["AXRole": "AXButton", "AXIdentifier": "old"]))
        let context = ApplicationElements.childContext(parent)
        let original = ApplicationElements.Target(application: app, launched: parent.launched,
            element: child, scope: parent.scope, role: "AXButton", identity: identity,
            ancestors: [window], context: context)
        let key = ApplicationElements.Identity(pid: app.processIdentifier, launched: parent.launched,
            element: child, scope: parent.scope)
        let old = registry.observe(key, evidence: .init(identity: identity, provider: nil, context: context),
            value: original)
        var attributes = AXAttributeReader.API()
        attributes.batch = { element, fields in
            if CFEqual(element, child) { return (.cannotComplete, nil) }
            return (.success, fields.map { _ in "AXWindow" } as CFArray)
        }
        attributes.single = { element, field in
            if CFEqual(element, child), let value = display[field] { return (.success, value as CFString) }
            return (.cannotComplete, nil)
        }
        var relations = AXRelationReader.API()
        relations.count = { element, property in
            (.success, CFEqual(element, window) && property == kAXChildrenAttribute ? 1 : 0)
        }
        relations.range = { _, _, _ in (.success, [child] as CFArray) }
        relations.single = { _, _ in (.noValue, nil) }
        var fallbackReads = 0
        let collector = NativeFactCollector(registry: registry, inspection: .init(registry: registry),
            attributes: attributes, relations: relations, readIdentity: { element in
                precondition(CFEqual(element, child))
                fallbackReads += 1
                return fallback
            })
        let result = try collector.collect(ref, session: "budget-session", fields: ["AXRole", "AXIdentifier"],
            maxNodes: 2, maxDepth: 1, actionDetail: .none)
        let nodes = result["nodes"] as! [[String: Any]]
        precondition(fallbackReads == 1, "weak display identity must still allow an independent recovery")
        guard let expected else {
            precondition(nodes.count == 1 && registry.target(old) != nil,
                "contradictory samples cannot publish or retire either candidate")
            let links = result["relations"] as! [[String: Any]]
            let children = links.first { $0["source"] as? String == ref && $0["property"] as? String == kAXChildrenAttribute }
            precondition(children?["state"] as? String == "partial"
                && (children?["detail"] as? String)?.contains("samples disagreed") == true)
            return
        }
        precondition(nodes.count == 2, "the independently sampled child must stay observable")
        let observed = nodes[1]["node"] as! String
        precondition(observed != old && registry.target(old) == nil,
            "a failed independent identity fallback must not publish changed child facts under the original token")
        precondition(registry.target(observed)?.identity.attributes == expected)
    }

    @MainActor private static func closeButtonUsesSingleObjectGetter() throws {
        let element = AXUIElementCreateApplication(NSRunningApplication.current.processIdentifier)
        var api = AXRelationReader.API()
        api.count = { _, _ in preconditionFailure("AXCloseButton is not an array") }
        api.range = { _, _, _ in preconditionFailure("AXCloseButton is not an array") }
        api.single = { _, property in
            precondition(property == "AXCloseButton")
            return (.success, element)
        }
        let reader = AXRelationReader(api: api)
        guard case .page(let page) = try reader.read(element, source: "window", property: "AXCloseButton", limit: 1)
        else { preconditionFailure("the exact close button must be returned independently of AXChildren") }
        precondition(page.total == 1 && page.targets.count == 1 && CFEqual(page.targets.first!, element))
        precondition(page.nativeAPI == "AXUIElementCopyAttributeValue" && page.isArray == false)
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
