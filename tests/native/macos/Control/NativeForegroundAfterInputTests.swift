// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit

@main enum NativeForegroundAfterInputTests {
    @MainActor final class Scene {
        let original = identity(101), target = identity(102), user = identity(103)
        var foreground: pid_t? = 101
        var counters: [UInt32] = [0, 0, 0, 0, 0, 0]
        var activations = 0, inspections = 0
        var reasons = [String]()
        var cancelled = false
        lazy var owner = NativeForegroundResponsibility(
            snapshot: { .init(foregroundPID: self.foreground, activity: self.counters) },
            process: { [self] pid in [original, target, user].first { $0.pid == pid } },
            check: { if self.cancelled { throw CancellationError() } }, watchActivation: false)

        func begin(requested: Bool = true) {
            owner.recordPreparation(target: target, foreground: true)
            foreground = target.pid
            if requested { owner.recordRequestedInput() }
        }

        func finish(
            input: NativeForegroundRestorationScope.InputState = .completed,
            result: NativeForegroundResult.Decision = .persistentResult
        ) async -> Bool {
            let state = owner.state(target: target)
            return await NativeForegroundRestorationScope.finishPhase(
                input: input, activity: owner.activityState(state.activity),
                inspect: { self.inspections += 1; return result },
                restore: {
                    let outcome = await NativeForegroundRestorationScope.restore(
                        originalPID: self.original.pid, targetPID: self.target.pid, touched: true,
                        initialActivity: self.owner.expectedActivity, sample: { self.owner.state(target: self.target) },
                        check: self.owner.checkRestoration,
                        activate: { self.activations += 1; self.foreground = self.original.pid; return true }, wait: {})
                    self.owner.recordRestoration(outcome)
                    return outcome == .restored
                }, retain: { self.reasons.append($0) })
        }
    }

    static func expect(_ condition: Bool) { precondition(condition) }

    static func identity(_ pid: pid_t) -> NativeProcessIdentity {
        .init(pid: pid, parent: 1, seconds: 1, microseconds: 0, executable: "/owned/\(pid)")
    }

    static func decisions() {
        typealias Result = NativeForegroundResult
        let standard = Result.Window(id: 2, role: kAXWindowRole, subrole: kAXStandardWindowSubrole, modal: nil)
        let dialog = Result.Window(id: 2, role: kAXWindowRole, subrole: kAXDialogSubrole, modal: true)
        for value in [standard, dialog] {
            precondition(Result.decide(before: [1], after: [1, 2], windows: [value], knownTransient: false) == .persistentResult)
        }
        for value in [
            Result.Window(id: 2, role: kAXWindowRole, subrole: kAXDialogSubrole, modal: false),
            Result.Window(id: 2, role: kAXWindowRole, subrole: kAXSystemDialogSubrole, modal: nil),
            Result.Window(id: 2, role: nil, subrole: nil, modal: nil)
        ] {
            precondition(Result.decide(before: [1], after: [1, 2], windows: [value], knownTransient: false) == .unknownResult)
        }
        precondition(Result.decide(before: [1], after: [1], windows: [], knownTransient: false) == .noProvenPersistentResult)
        precondition(Result.decide(before: [1], after: [1], windows: [], knownTransient: true) == .transientResult)
        precondition(Result.decide(before: nil, after: [1, 2], windows: [standard], knownTransient: false) == .unknownResult)
        precondition(Result.decide(before: [1], after: [1, 2, 3], windows: [standard], knownTransient: false) == .unknownResult)
        precondition(Result.decide(before: [1], after: [1, 2], windows: [standard], knownTransient: true) == .transientResult)
    }

    @MainActor static func main() async throws {
        decisions()
        let ordinary = Scene(); ordinary.begin()
        expect(await ordinary.finish())
        precondition(ordinary.activations == 1 && ordinary.foreground == ordinary.original.pid)
        for reason in [NativeForegroundResult.Decision.noProvenPersistentResult, .transientResult, .unknownResult] {
            let scene = Scene(); scene.begin()
            expect(await !scene.finish(result: reason))
            precondition(scene.activations == 0 && scene.reasons == [reason.rawValue])
        }
        let own = Scene(); own.begin()
        own.owner.willPostGlobalInput(.leftMouseDown)
        precondition(own.owner.activityState(own.counters) == .ownInputPending)
        precondition(!own.owner.state(target: own.target).externalActivation)
        own.counters[0] += 1 // actual own HID post becomes observable
        own.owner.willPostGlobalInput(.leftMouseUp)
        expect(await own.finish())
        precondition(own.activations == 1)
        let pending = Scene(); pending.begin(); pending.owner.willPostGlobalInput(.leftMouseDown)
        expect(await !pending.finish())
        precondition(pending.reasons == ["ownHIDCounterPending"] && pending.inspections == 0 && pending.activations == 0)
        let exposure = Scene(); exposure.begin(requested: false); exposure.owner.willPostGlobalInput(.scrollWheel)
        expect(await !exposure.finish(input: .notDispatched))
        precondition(exposure.reasons == ["ownHIDCounterPending"] && exposure.activations == 0)
        precondition(!exposure.owner.state(target: exposure.target).externalActivation)
        let hardware = Scene(); hardware.begin(); hardware.owner.willPostGlobalInput(.leftMouseDown)
        hardware.counters[0] = 2 // own down plus independent physical click
        expect(await !hardware.finish())
        precondition(hardware.reasons == ["userTakeover"] && hardware.activations == 0)
        for index in [3, 4, 5] {
            let activity = Scene(); activity.begin(); activity.counters[index] += 1
            expect(await !activity.finish())
            precondition(activity.reasons == ["userTakeover"] && activity.activations == 0)
        }
        let thirdApp = Scene(); thirdApp.begin(); thirdApp.foreground = thirdApp.user.pid
        thirdApp.owner.recordActivation(thirdApp.user)
        expect(await !thirdApp.finish())
        precondition(thirdApp.activations == 0 && !thirdApp.owner.permitsForegroundPreparation())
        let uncertain = Scene(); uncertain.begin()
        expect(await !uncertain.finish(input: .uncertain))
        precondition(uncertain.inspections == 0 && uncertain.activations == 0)
        let cancelled = Scene(); cancelled.begin(); cancelled.cancelled = true
        expect(await !cancelled.finish())
        precondition(cancelled.activations == 0)
        let receipt = NativeExecutionReceipt(target: [:], onEffect: {})
        receipt.recordAccepted(); receipt.dispatch = "dispatched"
        do {
            try NativeForegroundRestorationScope.requireSettledActivation(true)
            preconditionFailure("a pending foreground return cannot be claimed completed")
        } catch let failure as DriverError {
            precondition(!failure.description.contains("input was not sent"))
            precondition(failure.code == .executionUnknown && failure.effect == .possible)
            receipt.recordFailure(failure)
        }
        precondition(receipt.dispatch == "dispatched")
        print("PASS post-input persistent result restores once; inline/unknown/user/pending retain without replay or counter confusion")
    }
}
