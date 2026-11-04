// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit

@main enum NativeForegroundRestorationTests {
    static func expect(_ condition: Bool) { precondition(condition) }
    @MainActor static func main() async {
        precondition(NativeForegroundResult.persistentWindow(role: kAXWindowRole, subrole: kAXStandardWindowSubrole, modal: nil))
        precondition(NativeForegroundResult.persistentWindow(role: kAXWindowRole, subrole: kAXDialogSubrole, modal: true))
        precondition(!NativeForegroundResult.persistentWindow(role: kAXWindowRole, subrole: kAXDialogSubrole, modal: false))
        precondition(!NativeForegroundResult.persistentWindow(role: kAXMenuRole, subrole: nil, modal: nil))
        precondition(!NativeForegroundResult.persistentWindow(role: kAXWindowRole, subrole: nil, modal: nil))
        await returnsOnlyOwnedForeground()
        await preservesNewOwner()
        await noReplayAfterUnconfirmedActivation()
        await delayedActivationMustSettleBeforeInput()
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
        precondition(duringSend.unsettled, "user takeover does not withdraw an already queued activation")

        let cancelled = RestorationProbe()
        cancelled.onCheck = { throw CancellationError() }
        expect(await cancelled.run() == .unknown)
        precondition(cancelled.activations == 0 && !cancelled.unsettled)
    }

    @MainActor static func noReplayAfterUnconfirmedActivation() async {
        let ignored = RestorationProbe()
        expect(await ignored.run() == .unknown)
        precondition(ignored.activations == 1 && ignored.waits == 39 && ignored.unsettled)
        let thrown = RestorationProbe()
        thrown.onActivate = { throw DriverError("receipt lost", code: .executionUnknown) }
        expect(await thrown.run() == .unknown)
        precondition(thrown.activations == 1 && thrown.waits == 39 && thrown.unsettled)
        let refused = RestorationProbe()
        refused.onActivate = { false }
        expect(await refused.run() == .failed)
        precondition(refused.activations == 1 && refused.waits == 0 && !refused.unsettled)
        let rejected = RestorationProbe()
        rejected.onActivate = { throw DriverError("helper did not start", code: .inputUnavailable) }
        expect(await rejected.run() == .unknown)
        precondition(!rejected.unsettled, "a definite pre-call failure cannot block independent input")
    }

    @MainActor static func delayedActivationMustSettleBeforeInput() async {
        let delayed = RestorationProbe()
        delayed.onWait = {
            if delayed.waits == 20 { delayed.state.foregroundPID = 100 }
        }
        expect(await delayed.run() == .restored)
        precondition(delayed.activations == 1 && delayed.waits == 20 && !delayed.unsettled)
        let lostReceipt = RestorationProbe()
        lostReceipt.onActivate = { throw DriverError("receipt lost", code: .executionUnknown, effect: .possible) }
        lostReceipt.onWait = {
            if lostReceipt.waits == 20 { lostReceipt.state.foregroundPID = 100 }
        }
        expect(await lostReceipt.run() == .restored)
        precondition(lostReceipt.activations == 1 && !lostReceipt.unsettled)
        do {
            try NativeForegroundRestorationScope.requireSettledActivation(true)
            preconditionFailure("unsettled activation must stop before new input opens a popup")
        } catch let failure as DriverError {
            precondition(failure.code == .executionUnknown && failure.effect == .possible)
        } catch { preconditionFailure("unexpected pending-activation classification") }
    }
}

@MainActor private final class RestorationProbe {
    var state = NativeForegroundRestorationScope.State(
        foregroundPID: 200, originalCurrent: true, targetCurrent: true, activity: [0], externalActivation: false)
    var activations = 0, waits = 0
    var unsettled = false
    var onCheck: () throws -> Void = {}
    var onActivate: () throws -> Bool = { true }
    var onWait: () -> Void = {}

    func run(touched: Bool = true, original: pid_t = 100) async -> NativeForegroundRestorationScope.Outcome {
        await NativeForegroundRestorationScope.restore(
            originalPID: original, targetPID: 200, touched: touched, initialActivity: [0], sample: { self.state },
            check: onCheck, activate: {
                self.activations += 1
                return try self.onActivate()
            }, wait: { self.waits += 1; self.onWait() }, onUnsettled: { self.unsettled = true })
    }
}
