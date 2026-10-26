@main struct NativeCurrentReceiverEvidenceTests {
    static func main() throws {
        var parents = [
            "editor": "wrapper", "wrapper": "scope", "outside": "other", "other": "window", "nested": "sheet",
            "sheet": "scope", "cycle": "cycle"
        ]
        let roles = ["wrapper": "AXGroup", "other": "AXGroup", "window": "AXWindow", "sheet": "AXSheet"]
        var focus: String? = "editor", reads = [String](), local: Bool? = true
        func observe() throws -> NativeCurrentReceiverEvidence.Observation {
            try NativeCurrentReceiverEvidence.observe(
                scope: "scope", focus: { focus }, same: ==,
                parent: {
                    reads.append($0)
                    return parents[$0]
                }, role: { roles[$0] }, locallyFocused: { _ in local }, register: { $0 }, check: {})
        }
        func expect(_ state: String) throws {
            let observed = try observe()
            precondition(observed.state == state, observed.detail)
        }
        let result = try observe()
        precondition(result.state == "confirmed" && result.node == "editor" && result.ancestors == ["wrapper", "scope"])
        precondition(reads == ["editor", "wrapper"], "Only the positive scope path may be queried")
        // A large / unavailable unrelated subtree and unknown ancestors above
        // scope cannot affect this query: the evidence port has no children API.
        for i in 0..<500 { parents["unrelated-\(i)"] = "scope" }
        reads = []
        local = nil
        try expect("confirmed")
        precondition(reads == ["editor", "wrapper"])
        local = false
        try expect("conflict")
        local = true
        for candidate in ["outside", "nested"] {
            focus = candidate
            try expect("conflict")
        }
        for candidate in ["missing", "cycle"] {
            focus = candidate
            try expect("unavailable")
        }
        focus = nil
        try expect("absent")
        focus = "editor"
        parents["wrapper"] = nil
        try expect("unavailable")
        parents["wrapper"] = "scope"
        try expect("confirmed")
        var samples = 0
        let moved = try NativeCurrentReceiverEvidence.observe(
            scope: "scope",
            focus: {
                samples += 1
                return samples == 1 ? "editor" : "other"
            }, same: ==, parent: { parents[$0] }, role: { roles[$0] }, locallyFocused: { _ in true }, register: { $0 },
            check: {})
        precondition(moved.state == "conflict")
        enum Cancelled: Error { case request }
        do {
            _ = try NativeCurrentReceiverEvidence.observe(
                scope: "scope", focus: { "editor" }, same: ==, parent: { parents[$0] }, role: { roles[$0] },
                locallyFocused: { _ in true }, register: { $0 }, check: { throw Cancelled.request })
            preconditionFailure("Cancellation must propagate")
        } catch Cancelled.request {}
        print("PASS scope-bounded current receiver evidence: positive path, conflicts, gaps, recovery, cancellation")
    }
}
