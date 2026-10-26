/// Entry conditions of one selected native unit, for the operation's immutable
/// intent. Unknown evidence stays unknown; readiness is never dispatch authority.
struct NativeReadiness {
    let state: String
    let pending: [String]

    init(_ conditions: [(String, String)] = []) {
        pending = conditions.filter { $0.1 != "yes" }.map { $0.0 }
        state = conditions.contains { $0.1 == "no" } ? "no" : pending.isEmpty ? "yes" : "unknown"
    }

    var ready: Bool { state == "yes" }

    /// An unavailable route is readiness evidence; cancellation, expiry and
    /// identity/policy failures are not. Recheck the request after synchronous
    /// native queries before interpreting their result as a focus problem.
    static func probeRoute(check: () throws -> Void, probe: () throws -> Bool) throws -> Bool {
        try check()
        let ready: Bool
        do { ready = try probe() } catch {
            try check()
            guard (error as? DriverError)?.code == .inputUnavailable else { throw error }
            return false
        }
        try check()
        return ready
    }

    func requiringEnabled(_ enabled: Bool?) -> NativeReadiness {
        guard enabled == false else { return self }
        return NativeReadiness(pending.filter { $0 != "enabled" }.map { ($0, state) } + [("enabled", "no")])
    }
}
