import ApplicationServices

@main struct AXAttributeReaderTests {
    @MainActor static func main() throws {
        let element = AXUIElementCreateApplication(100)
        var calls = [String]()
        var api = AXAttributeReader.API()
        api.batch = { _, _ in
            calls.append("batch")
            return (.notImplemented, nil)
        }
        api.single = { _, name in
            calls.append(name)
            return name == "AXRole" ? (.success, "AXButton" as CFString) : (.noValue, nil)
        }
        let recovered = try AXAttributeReader.read(
            element, fields: ["AXRole", "AXTitle"], api: api, withinBudget: { true })
        assert(calls == ["batch", "AXRole", "AXTitle"])
        assert(recovered.status == .notImplemented && recovered.fields[0].value as? String == "AXButton")
        assert(recovered.fields[0].batchIndex == nil && recovered.fields[1].status == .noValue)

        calls = []
        api.batch = { _, _ in
            calls.append("batch")
            return (.success, ["wrong slot"] as CFArray)
        }
        let malformed = try AXAttributeReader.read(
            element, fields: ["AXRole", "AXTitle"], api: api, withinBudget: { true })
        assert(malformed.fields[0].value as? String == "AXButton", "a malformed batch must not misbind a slot")
        assert(calls == ["batch", "AXRole", "AXTitle"])

        calls = []
        var error = AXError.attributeUnsupported
        let wrapped = AXValueCreate(.axError, &error)!
        api.batch = { _, _ in
            calls.append("batch")
            return (.success, ["AXButton" as CFString, wrapped] as CFArray)
        }
        let partial = try AXAttributeReader.read(
            element, fields: ["AXRole", "AXTitle"], api: api, withinBudget: { true })
        assert(calls == ["batch"], "ordinary per-slot errors do not fan out into individual queries")
        assert(partial.fields[1].batchIndex == 1 && CFEqual(partial.fields[1].value!, wrapped))

        calls = []
        api.batch = { _, _ in
            calls.append("batch")
            return (.invalidUIElement, nil)
        }
        let invalid = try AXAttributeReader.read(element, fields: ["AXRole"], api: api, withinBudget: { true })
        assert(calls == ["batch"] && invalid.fields[0].status == .invalidUIElement)

        calls = []
        var checks = 0
        api.batch = { _, _ in
            calls.append("batch")
            return (.cannotComplete, nil)
        }
        let limited = try AXAttributeReader.read(
            element, fields: ["AXRole", "AXTitle", "AXValue"], api: api,
            withinBudget: {
                checks += 1
                return checks <= 2
            })
        assert(calls == ["batch", "AXRole"] && limited.fields.dropFirst().allSatisfy(\.timedOut))
        calls = []
        let skipped = try AXAttributeReader.read(element, fields: ["AXRole"], api: api, withinBudget: { false })
        assert(calls.isEmpty && !skipped.queried && skipped.fields[0].timedOut)
        print("PASS AX attributes: batch/individual strategies, malformed slots, errors and shared deadline")
    }
}
