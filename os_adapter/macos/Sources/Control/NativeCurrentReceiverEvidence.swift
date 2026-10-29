// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

/// A bounded positive focus path. It never inventories sibling subtrees or
/// interprets a missing optional observation as destruction of the scope.
enum NativeCurrentReceiverEvidence {
    struct Observation {
        let state: String
        var node: String? = nil
        var ancestors: [String] = []
        var detail: String = ""
    }

    static func observe<Element>(
        scope: Element, focus: () throws -> Element?, same: (Element, Element) -> Bool,
        parent: (Element) throws -> Element?, role: (Element) throws -> String?,
        locallyFocused: (Element) throws -> Bool?, register: (Element) throws -> String, check: () throws -> Void
    ) throws -> Observation {
        guard let initial = try focus() else {
            return Observation(state: "absent", detail: "The application reports no current focus.")
        }
        if try locallyFocused(initial) == false {
            return Observation(
                state: "conflict", detail: "The reported current receiver contradicts its local focus state.")
        }
        var chain = [Element](), current = initial
        for _ in 0..<128 {
            try check()
            if same(current, scope) {
                guard let final = try focus(), same(initial, final), try locallyFocused(final) != false else {
                    return Observation(state: "conflict", detail: "The current receiver changed during discovery.")
                }
                // Registration is bounded to this positive path. A failure here
                // invalidates only this observation, never its original scope.
                return Observation(state: "confirmed", node: try register(initial), ancestors: try chain.map(register))
            }
            guard let next = try parent(current) else {
                return Observation(
                    state: "unavailable",
                    detail: "The current receiver parent path is unavailable before the requested scope.")
            }
            if same(next, initial) || chain.contains(where: { same($0, next) }) {
                return Observation(state: "unavailable", detail: "The current receiver parent path is cyclic.")
            }
            if !same(next, scope) {
                guard let value = try role(next) else {
                    return Observation(
                        state: "unavailable", detail: "The receiver path has an unknown window boundary.")
                }
                if ["AXApplication", "AXWindow", "AXSheet"].contains(value) {
                    return Observation(
                        state: "conflict", detail: "The current receiver belongs to another container or window.")
                }
            }
            chain.append(next)
            current = next
        }
        return Observation(state: "unavailable", detail: "The current receiver path exceeded its bounded query depth.")
    }
}
