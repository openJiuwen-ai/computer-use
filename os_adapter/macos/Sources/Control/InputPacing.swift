import Foundation

/// Let queued control events run between inputs, then count preflight work
/// toward the existing interval. Every suspension precedes fresh validation.
@MainActor enum InputPacing {
    static func validate(
        notBefore: TimeInterval?, now: () -> TimeInterval = { ProcessInfo.processInfo.systemUptime },
        wait: (TimeInterval) async throws -> Void = { try await Task.sleep(for: .seconds($0)) },
        check: () async throws -> Void
    ) async throws {
        if notBefore != nil {
            // Slow synchronous AX checks can consume the whole pacing interval
            // without ever suspending. Cancel/EOF/signal callbacks also use the
            // main queue, so merely calling an async function (or Task.yield)
            // does not guarantee that they can run before the next character.
            await withCheckedContinuation { continuation in DispatchQueue.main.async { continuation.resume() } }
        }
        while true {
            try Task.checkCancellation()
            try await check()
            guard let notBefore else { return }
            let remaining = notBefore - now()
            guard remaining > 0 else { return }
            try await wait(remaining)
        }
    }
}
