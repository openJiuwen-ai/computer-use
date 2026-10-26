import Foundation

/// One delivery owns a finite read-only recovery allowance, shared by every
/// character. This gate never prepares, posts, changes route or resets deadline.
@MainActor final class NativeKeyboardReadinessGate {
    enum Evidence { case ready, unavailable(String), conflicting(String) }
    enum Failure: Error { case unavailable(String), conflicting(String) }
    private(set) var remainingWaits: Int
    let interval: TimeInterval

    init(waits: Int = 6, interval: TimeInterval = 0.025) {
        remainingWaits = max(0, waits)
        self.interval = max(0, interval)
    }

    /// Slow mechanism queries run before the final fresh receiver snapshot.
    static func queryThenReceiver<State>(
        mechanism: () throws -> Evidence, receiver: () throws -> State, validate: (inout State) throws -> Evidence
    ) rethrows -> Evidence {
        let evidence = try mechanism()
        guard case .ready = evidence else { return evidence }
        var current = try receiver()
        return try validate(&current)
    }

    func confirm(
        check: () throws -> Void, sample: () throws -> Evidence,
        wait: (TimeInterval) async throws -> Void = { try await Task.sleep(for: .seconds($0)) }
    ) async throws {
        while true {
            try check()
            switch try sample() {
            case .ready:
                try check()
                return
            case .conflicting(let detail): throw Failure.conflicting(detail)
            case .unavailable(let detail):
                guard remainingWaits > 0 else { throw Failure.unavailable(detail) }
                remainingWaits -= 1
                try check()
                try await wait(interval)
            }
        }
    }
}
