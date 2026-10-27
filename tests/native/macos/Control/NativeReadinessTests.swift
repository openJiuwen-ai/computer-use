import Foundation

@main struct NativeReadinessTests {
    static func main() throws {
        precondition(NativeReadiness().ready)
        let input = [("receiver", "yes"), ("selection", "no")]
        let command = NativeReadiness(input)
        precondition(command.state == "no" && command.pending == ["selection"])
        let unknown = NativeReadiness([("receiver", "yes"), ("selection", "unknown")])
        precondition(unknown.state == "unknown" && unknown.pending == ["selection"])
        let changed = NativeReadiness([("receiver", "no"), ("selection", "unknown"), ("geometry", "yes")])
        precondition(changed.state == "no" && changed.pending == ["receiver", "selection"])
        let ready = NativeReadiness([("receiver", "yes"), ("selection", "yes"), ("geometry", "yes")])
        precondition(ready.ready && ready.pending.isEmpty)
        precondition(ready.requiringEnabled(false).pending == ["enabled"])
        precondition(unknown.requiringEnabled(false).pending == ["selection", "enabled"])
        precondition(unknown.requiringEnabled(false).state == "no")
        precondition(unknown.requiringEnabled(true).state == "unknown")
        precondition(ready.requiringEnabled(nil).ready)
        for value in [true, false] {
            let result = try NativeReadiness.probeRoute(check: {}, probe: { value })
            precondition(result == value)
        }
        let unavailable = try NativeReadiness.probeRoute(
            check: {}, probe: { throw DriverError("no native window route", code: .inputUnavailable) })
        precondition(!unavailable)
        for code: FailureCode in [.staleTarget, .desktopUnavailable, .modeUnsupported, .executionFailed] {
            do {
                _ = try NativeReadiness.probeRoute(
                    check: {}, probe: { throw DriverError("original failure", code: code) })
                fatalError("route inspection swallowed \(code)")
            } catch let error as DriverError {
                precondition(error.code == code && error.description == "original failure")
            }
        }
        do {
            _ = try NativeReadiness.probeRoute(check: {}, probe: { throw CancellationError() })
            fatalError("cancelled route became an unavailable route")
        } catch is CancellationError {}
        // A synchronous native query can outlive its request. Neither a
        // successful response nor an unavailable route may conceal that expiry.
        struct Expired: Error {}
        for queryFails in [false, true] {
            var expired = false, queries = 0
            do {
                _ = try NativeReadiness.probeRoute(
                    check: { if expired { throw Expired() } },
                    probe: {
                        queries += 1
                        expired = true
                        if queryFails { throw DriverError("no route", code: .inputUnavailable) }
                        return true
                    })
                fatalError("expired route query became readiness evidence")
            } catch is Expired { precondition(queries == 1) }
        }
        var queried = false
        do {
            _ = try NativeReadiness.probeRoute(
                check: { throw Expired() },
                probe: {
                    queried = true
                    return true
                })
            fatalError("expired request entered route query")
        } catch is Expired { precondition(!queried) }
        print("PASS per-unit readiness preserves known failures and unknown evidence")
    }
}
