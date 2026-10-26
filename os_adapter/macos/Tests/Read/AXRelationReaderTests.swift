import ApplicationServices

@main struct AXRelationReaderTests {
    @MainActor static func main() throws {
        let reader = AXRelationReader()
        // Opaque identities only; these tests do not query or operate the desktop.
        let a = AXUIElementCreateApplication(100), b = AXUIElementCreateApplication(101)
        let partial = reader.encode(
            .init(source: "source", property: "AXChildren", offset: 0, total: 3, prefix: [a, b]), targets: ["a"])
        assert(partial["offset"] as? Int == 0 && partial["state"] as? String == "partial")
        assert(partial["cursor"] is String)
        assert(partial["targets"] as? [String] == ["a"], "unregistered targets must not be consumed")
        for index in 0..<512 {
            let unexpanded = reader.encode(
                .init(source: "unexpanded-\(index)", property: "AXChildren", offset: 0, total: 2, prefix: []),
                targets: [])
            assert(unexpanded["state"] as? String == "partial" && unexpanded["total"] as? Int == 2)
            assert(unexpanded["cursor"] == nil, "an empty prefix has no native identities to retain")
        }
        var available = [partial]
        reader.retainAvailableCursors(&available)
        assert(
            available[0]["cursor"] as? String == partial["cursor"] as? String,
            "unexpanded descendants must not evict an already-consumed parent prefix")
        let empty = reader.encode(
            .init(source: "source", property: "AXChildren", offset: 0, total: 0, prefix: []), targets: [])
        assert(empty["state"] as? String == "complete" && empty["cursor"] == nil)
        let last = reader.encode(
            .init(source: "source", property: "AXChildren", offset: 1, total: 2, prefix: [a, b]), targets: ["b"])
        assert(last["offset"] as? Int == 1 && last["state"] as? String == "partial" && last["cursor"] == nil)
        var links = [partial]
        for index in 0..<300 {
            _ = reader.encode(
                .init(source: "node-\(index)", property: "AXChildren", offset: 0, total: 2, prefix: [a]), targets: ["a"]
            )
        }
        reader.retainAvailableCursors(&links)
        assert(links[0]["cursor"] == nil, "evicted continuations must not be advertised at publication")
        assert(links[0]["targets"] as? [String] == ["a"])
        try acquisition()
        print("PASS AX relation pages: fallback acquisition, changing lists, identity checks and honest continuations")
    }

    @MainActor static func acquisition() throws {
        let a = AXUIElementCreateApplication(100), b = AXUIElementCreateApplication(101),
            c = AXUIElementCreateApplication(102)
        var calls = [String]()
        var api = AXRelationReader.API()
        api.count = { _, _ in
            calls.append("count")
            return (.notImplemented, 0)
        }
        api.range = { _, _, _ in fatalError("count failure must select the ordinary getter") }
        api.single = { _, _ in
            calls.append("single")
            return (.success, [a, b, c] as CFArray)
        }
        let reader = AXRelationReader(api: api)
        guard case .page(let recovered) = try reader.read(a, source: "root", property: "AXChildren", limit: 2) else {
            fatalError()
        }
        assert(calls == ["count", "single"] && recovered.total == 3 && recovered.prefix.count == 2)
        let first = reader.encode(recovered, targets: ["a", "b"])
        assert(first["nativeAPI"] as? String == "AXUIElementCopyAttributeValue" && first["cursor"] is String)
        guard
            case .page(let last) = try reader.read(
                a, source: "root", property: "AXChildren", limit: 2, cursor: first["cursor"] as? String)
        else { fatalError() }
        assert(last.offset == 2 && last.targets.count == 1 && CFEqual(last.targets.first!, c))
        calls = []
        _ = try reader.read(a, source: "metadata", property: "AXChildren", limit: 0)
        assert(calls == ["count"], "a metadata-only read must not load an unbounded fallback array")

        calls = []
        api.count = { _, _ in
            calls.append("count")
            return (.success, 3)
        }
        api.range = { _, _, _ in
            calls.append("range")
            return (.cannotComplete, nil)
        }
        guard
            case .page(let ranged) = try AXRelationReader(api: api).read(
                a, source: "root", property: "AXChildren", limit: 1)
        else { fatalError() }
        assert(calls == ["count", "range", "single"] && ranged.prefix.count == 1)

        var counts = 0
        api.count = { _, _ in
            counts += 1
            return counts == 1 ? (.success, 3) : (.cannotComplete, 0)
        }
        api.range = { _, _, length in (.success, Array([a, b, c].prefix(length)) as CFArray) }
        api.single = { _, _ in fatalError("do not replay a successful range because its consistency check failed") }
        let changingReader = AXRelationReader(api: api)
        guard case .page(let uncertain) = try changingReader.read(a, source: "root", property: "AXChildren", limit: 2)
        else { fatalError() }
        let captured = changingReader.encode(uncertain, targets: ["a", "b"])
        assert(uncertain.prefix.count == 2 && captured["state"] as? String == "partial" && captured["cursor"] == nil)

        counts = 0
        api.count = { _, _ in
            counts += 1
            return (.success, counts == 1 ? 3 : 4)
        }
        guard
            case .page(let grew) = try AXRelationReader(api: api).read(
                a, source: "root", property: "AXChildren", limit: 3)
        else { fatalError() }
        assert(grew.prefix.count == 3 && grew.issue != nil, "count drift must retain the successful getter's payload")

        var order = [a, b, c]
        api.count = { _, _ in (.success, 3) }
        api.range = { _, _, length in (.success, Array(order.prefix(length)) as CFArray) }
        let identityReader = AXRelationReader(api: api)
        guard case .page(let initial) = try identityReader.read(a, source: "root", property: "AXChildren", limit: 1)
        else { fatalError() }
        let cursor = identityReader.encode(initial, targets: ["a"])["cursor"] as! String
        order = [b, a, c]
        do {
            _ = try identityReader.read(a, source: "root", property: "AXChildren", limit: 1, cursor: cursor)
            fatalError("reordered continuations must still be rejected")
        } catch let error as DriverError { assert(error.code == .snapshotExpired) }

        api.count = { _, _ in (.illegalArgument, 0) }
        api.single = { _, property in property == "AXAuditOwner" ? (.success, b) : (.success, [a, b] as CFArray) }
        let dynamic = AXRelationReader(api: api)
        guard case .page(let owner) = try dynamic.read(a, source: "root", property: "AXAuditOwner", limit: 1),
            case .page(let items) = try dynamic.read(a, source: "root", property: "AXAuditItems", limit: 1)
        else { fatalError() }
        assert(owner.isArray == false && items.isArray == true)
        assert(dynamic.encode(owner, targets: ["b"])["nativeType"] as? String == "AXUIElement")
        assert(dynamic.encode(items, targets: ["a"])["nativeType"] as? String == "CFArray")
    }
}
