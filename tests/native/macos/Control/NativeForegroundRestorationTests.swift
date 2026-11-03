// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit

@main enum NativeForegroundRestorationTests {
    static func expect(_ condition: Bool) { precondition(condition) }
    @MainActor static func main() async {
        await returnsOnlyOwnedForeground()
        await preservesNewOwner()
        await noReplayAfterUnconfirmedActivation()
        print("PASS scoped foreground return preserves user ownership, lifecycle and bounded activation")
    }

    @MainActor static func returnsOnlyOwnedForeground() async {
        let probe = RestorationProbe()
        probe.onActivate = { probe.state.foregroundPID = 100; return true }
        expect(await probe.run() == .restored)
        precondition(probe.activations == 1 && probe.waits == 0)

        for (touched, original, front): (Bool, pid_t, pid_t) in [
            (false, 100, 200), (true, 200, 200), (true, 100, 100)
        ] {
            let unchanged = RestorationProbe()
            unchanged.state.foregroundPID = front
            expect(await unchanged.run(touched: touched, original: original) == .notNeeded)
            precondition(unchanged.activations == 0 && unchanged.waits == 0)
        }
        for originalCurrent in [false, true] {
            let stale = RestorationProbe()
            stale.state.originalCurrent = originalCurrent
            stale.state.targetCurrent = !originalCurrent
            expect(await stale.run() == .unavailable)
            precondition(stale.activations == 0, "a PID replacement must never receive restoration")
        }
    }

    @MainActor static func preservesNewOwner() async {
        for mode in 0..<3 {
            let user = RestorationProbe()
            if mode == 0 { user.state.foregroundPID = 300 }
            if mode == 1 { user.state.externalActivation = true }
            if mode == 2 { user.state.activity = [1] }
            expect(await user.run() == .ownershipLost)
            precondition(user.activations == 0)
        }
        let beforeSend = RestorationProbe()
        beforeSend.onCheck = { beforeSend.state.activity = [1] }
        expect(await beforeSend.run() == .ownershipLost)
        precondition(beforeSend.activations == 0, "recheck ownership immediately before requesting activation")

        let duringSend = RestorationProbe()
        duringSend.onActivate = { duringSend.state.foregroundPID = 300; return true }
        expect(await duringSend.run() == .ownershipLost)
        precondition(duringSend.activations == 1 && duringSend.waits == 0, "never fight the new application")

        let cancelled = RestorationProbe()
        cancelled.onCheck = { throw CancellationError() }
        expect(await cancelled.run() == .unknown)
        precondition(cancelled.activations == 0)
    }

    @MainActor static func noReplayAfterUnconfirmedActivation() async {
        let ignored = RestorationProbe()
        expect(await ignored.run() == .unknown)
        precondition(ignored.activations == 1 && ignored.waits == 11)
        let thrown = RestorationProbe()
        thrown.onActivate = { throw DriverError("receipt lost", code: .executionUnknown) }
        expect(await thrown.run() == .unknown)
        precondition(thrown.activations == 1 && thrown.waits == 0)
        let refused = RestorationProbe()
        refused.onActivate = { false }
        expect(await refused.run() == .failed)
        precondition(refused.activations == 1 && refused.waits == 0)
    }
}

@MainActor private final class RestorationProbe {
    var state = NativeBackgroundPreparationScope.State(
        foregroundPID: 200, originalCurrent: true, targetCurrent: true, activity: [0], externalActivation: false)
    var activations = 0, waits = 0
    var onCheck: () throws -> Void = {}
    var onActivate: () throws -> Bool = { true }

    func run(touched: Bool = true, original: pid_t = 100) async -> NativeBackgroundPreparationScope.Outcome {
        await NativeBackgroundPreparationScope.restore(
            originalPID: original, targetPID: 200, touched: touched, initialActivity: [0], sample: { self.state },
            check: onCheck, activate: {
                self.activations += 1
                return try self.onActivate()
            }, wait: { self.waits += 1 })
    }
}
