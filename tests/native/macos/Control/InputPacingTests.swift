import Foundation

@main struct InputPacingTests {
    enum Refusal: Error { case changed }

    @MainActor static func main() async throws {
        // Slow native checks already cover the release interval; no extra wait.
        var now = 0.0, checks = 0, waits = [Double]()
        try await InputPacing.validate(
            notBefore: 0.010, now: { now },
            wait: {
                waits.append($0)
                now += $0
            },
            check: {
                checks += 1
                now += 0.012
            })
        precondition(checks == 1 && waits.isEmpty && now >= 0.010)

        // A short preflight must be repeated after the remaining interval.
        now = 0
        checks = 0
        waits = []
        try await InputPacing.validate(
            notBefore: 0.010, now: { now },
            wait: {
                waits.append($0)
                now += $0
            },
            check: {
                checks += 1
                now += 0.003
            })
        precondition(checks == 2 && waits.count == 1 && abs(waits[0] - 0.007) < 1e-9)
        precondition(now >= 0.010)

        // A receiver changed while waiting cannot inherit the first check.
        now = 0
        checks = 0
        var changed = false, posted = false
        do {
            try await InputPacing.validate(
                notBefore: 0.010, now: { now },
                wait: {
                    now += $0
                    changed = true
                },
                check: {
                    checks += 1
                    if changed { throw Refusal.changed }
                })
            posted = true
        } catch Refusal.changed {}
        precondition(checks == 2 && !posted)

        // First input and ordinary chords retain exactly one preflight.
        checks = 0
        try await InputPacing.validate(
            notBefore: nil, now: { preconditionFailure("no pacing clock needed") },
            wait: { _ in preconditionFailure("no pacing wait needed") }, check: { checks += 1 })
        precondition(checks == 1)

        // Early wake-ups still cannot shorten the interval or skip validation.
        now = 0
        checks = 0
        waits = []
        try await InputPacing.validate(
            notBefore: 0.010, now: { now },
            wait: {
                waits.append($0)
                now += waits.count == 1 ? 0.004 : $0
            }, check: { checks += 1 })
        precondition(checks == 3 && waits.count == 2 && now >= 0.010)

        // Cancellation during pacing never reaches another validation or post.
        checks = 0
        posted = false
        do {
            try await InputPacing.validate(
                notBefore: 0.010, now: { 0 }, wait: { _ in throw CancellationError() }, check: { checks += 1 })
            posted = true
        } catch is CancellationError {}
        precondition(checks == 1 && !posted)

        // Native cancel/EOF callbacks arrive on the main queue. A sequence of
        // slow synchronous checks must not starve them merely because every
        // check has already consumed the inter-event interval.
        var sequence: Task<Void, Never>?
        var dispatched = 0, cancelled = false, cancellationReceived = false
        var clock = 0.0
        sequence = Task { @MainActor in
            do {
                for _ in 0..<500 {
                    try await InputPacing.validate(
                        notBefore: clock + 0.010, now: { clock },
                        wait: { _ in preconditionFailure("slow preflight needs no timer") },
                        check: {
                            clock += 0.012
                            if dispatched == 0 {
                                DispatchQueue.main.async {
                                    cancellationReceived = true
                                    sequence?.cancel()
                                }
                            }
                        })
                    dispatched += 1
                }
            } catch is CancellationError { cancelled = true } catch {
                preconditionFailure("unexpected error: \(error)")
            }
        }
        await sequence?.value
        precondition(cancellationReceived && cancelled && dispatched > 0 && dispatched < 500)

        // A queued receiver change is checked after the queue handoff, even
        // when the interval is already elapsed; no stale preflight is reused.
        changed = false
        posted = false
        DispatchQueue.main.async { changed = true }
        do {
            try await InputPacing.validate(
                notBefore: 0.010, now: { 1 }, check: { if changed { throw Refusal.changed } })
            posted = true
        } catch Refusal.changed {}
        precondition(changed && !posted)
        print(
            """
            PASS input pacing: slow/fast preflight, fresh validation after waits, refusal, early wake-up, \
            queued control events and cancellation
            """)
    }
}
