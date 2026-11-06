// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit

@main enum NativeForegroundResponsibilityTests {
    @MainActor final class Scene {
        let original = identity(101), user = identity(103)
        let target: NativeProcessIdentity
        var foreground: pid_t? = 101
        var activity: [UInt32] = [0]
        var processes: [pid_t: NativeProcessIdentity] = [:]
        var activations = 0, checks = 0
        var oldWindowExists = true

        init(target: NativeProcessIdentity = identity(102)) {
            self.target = target
            processes = [original.pid: original, target.pid: target, user.pid: user]
        }

        func owner() -> NativeForegroundResponsibility {
            NativeForegroundResponsibility(
                snapshot: { .init(foregroundPID: self.foreground, activity: self.activity) },
                process: { self.processes[$0] }, check: { self.checks += 1 }, watchActivation: false)
        }

        func recorder(step: Int64) -> NativeInputActivity {
            NativeInputActivity(operation: "operation", step: step, command: "test", intent: "click", target: [:],
                globalInputAllowed: true, snapshot: { ["foregroundPID": self.foreground as Any? ?? NSNull()] },
                emit: { _ in })
        }

        func restore(_ owner: NativeForegroundResponsibility) async -> NativeForegroundRestorationScope.Outcome {
            await NativeForegroundRestorationScope.restore(
                originalPID: owner.original!.pid, targetPID: target.pid, touched: true,
                initialActivity: owner.initialActivity, sample: { owner.state(target: self.target) },
                check: owner.checkRestoration,
                activate: { self.activations += 1; self.foreground = self.original.pid; return true }, wait: {})
        }
    }

    static func identity(_ pid: pid_t) -> NativeProcessIdentity {
        .init(pid: pid, parent: 1, seconds: 1, microseconds: 0, executable: "/fixture/\(pid)")
    }

    @MainActor static func operation(_ owner: NativeForegroundResponsibility) throws -> NativeOperation {
        let intent = try NativeIntent(["kind": "click", "parameters": ["button": "left", "count": 1]], mode: "write")
        return NativeOperation(timeoutMs: 1000, writable: true, intent: intent, target: [:],
            foregroundResponsibility: owner, release: {}, cancelActive: {})
    }

    @MainActor static func main() async throws {
        NSApplication.shared.setActivationPolicy(.prohibited)
        try await foregroundResponsibilitySurvivesCandidates()
        try await candidateDependencyExpiresWithoutDroppingDebt()
        try await sameCandidateKeepsForegroundDependencyAcrossSteps()
        try await firstClaimUsesCurrentUserContext()
        try await localPreparationDoesNotDemandIdleUser()
        try await userTakeoverOnlyConstrainsRestoration()
        try await endedTargetWindowDoesNotOwnRestoration()
        try await untouchedAndUnknownAreDistinct()
        try await menuResponsibilityContinuesOnlyAcrossRelatedObjectCalls()
        print("PASS operation foreground responsibility survives candidates without blind restoration or common input gates")
    }

    @MainActor static func menuResponsibilityContinuesOnlyAcrossRelatedObjectCalls() async throws {
        for interruption in ["none", "user", "global", "unrelated"] {
            let scene = Scene(), owner = scene.owner()
            let state = NativeDesktopState()
            let execution = NativeExecution(state: state, session: "menu-session", cancelActive: {})
            let binding: [String: Any] = ["session": "menu-session", "surface": "parent", "instance": "owner"]
            func make(_ target: [String: Any], global: Bool = false,
                      foreground: NativeForegroundResponsibility? = nil, mode: String = "write") throws -> NativeOperation {
                var raw: [String: Any] = ["kind": mode == "read" ? "read" : "click", "parameters": [:]]
                if global { raw["globalInput"] = true }
                let op = NativeOperation(timeoutMs: 3000, writable: mode == "write",
                    intent: try NativeIntent(raw, mode: mode), target: target,
                    foregroundResponsibility: foreground, release: {}, cancelActive: {})
                execution.registerNativeResourceScope(op, selection: NativeSelectionBasis(access: state.applications),
                                                      mode: mode, intent: mode == "read" ? "read" : "click")
                return op
            }
            let first = try make(binding, foreground: owner)
            _ = try await first.execute(step: 1, payload: Data([1])) {
                owner.recordPreparation(target: scene.target, foreground: true)
                scene.foreground = scene.target.pid
                owner.retainPresentation { .present }
                owner.recordRestoration(.retained)
                first.recordInputDispatch()
                return ["dispatch": "dispatched", "cleanup": "complete"]
            }
            _ = first.finish()
            precondition(scene.activations == 0, "closing an operation cannot operate the idle desktop")
            let read = try make(binding, mode: "read")
            _ = read.finish() // Pure observation does not discard or discharge the menu's owner.
            if interruption == "user" { scene.activity[0] += 1 }
            var next = binding
            if interruption == "unrelated" { next["surface"] = "another-document" }
            if interruption == "global" { next = ["session": "menu-session", "global": true] }
            let second = try make(next, global: interruption == "global")
            _ = try await second.execute(step: 1, payload: Data([2])) {
                let current = NativeForegroundResponsibility.current
                if interruption == "none" {
                    precondition(current === owner && current?.original == scene.original,
                                 "the second call recaptured the SDK's foreground as the user's original")
                    owner.recordRestoration(.restored)
                } else {
                    precondition(current !== owner, "interrupted responsibility survived into unrelated input")
                    if interruption == "global" { precondition(current == nil, "global input acquired object restoration") }
                }
                second.recordInputDispatch()
                return ["dispatch": "dispatched", "cleanup": "complete"]
            }
            _ = second.finish()
            execution.shutdown()
        }
    }

    @MainActor static func candidateDependencyExpiresWithoutDroppingDebt() async throws {
        let scene = Scene(), owner = scene.owner(), op = try operation(owner)
        _ = try await op.execute(step: 1, payload: Data([1])) {
            owner.requireForegroundForCandidate()
            owner.recordPreparation(target: scene.target, foreground: true)
            scene.foreground = scene.target.pid
            return ["dispatch": "notDispatched", "cleanup": "complete"]
        }
        _ = try await op.execute(step: 2, payload: Data([2])) {
            precondition(!owner.retainsForegroundForInput && owner.original == scene.original,
                         "a candidate dependency expires while operation foreground responsibility remains")
            let activity = scene.recorder(step: 2)
            owner.report(to: activity)
            precondition(activity.finish(dispatch: "notDispatched", cleanup: "complete")["foregroundRestoration"]
                         as? String == "retained")
            return ["dispatch": "notDispatched", "cleanup": "complete"]
        }
        _ = op.finish()
    }

    @MainActor static func sameCandidateKeepsForegroundDependencyAcrossSteps() async throws {
        let scene = Scene(target: NativeProcessIdentity.read(getpid())!)
        let owner = scene.owner(), op = try operation(owner)
        let candidate = "keyboard-window-command"
        _ = try await op.execute(step: 1, payload: Data([1]), candidateID: candidate) {
            owner.requireForegroundForCandidate()
            owner.recordPreparation(target: scene.target, foreground: true)
            scene.foreground = scene.target.pid
            return ["dispatch": "notDispatched", "cleanup": "complete"]
        }
        _ = try await op.execute(step: 2, payload: Data([2]), candidateID: candidate) {
            precondition(owner.hasForegroundObligation && owner.retainsForegroundForInput,
                         "receiver preparation and dispatch are steps of the same foreground-dependent candidate")
            let activity = scene.recorder(step: 2)
            await NativeInputActivity.$current.withValue(activity) {
                try! await NativeForegroundRestorationScope.perform(
                    target: BackgroundTarget(.current),
                    onPreparation: { _ in preconditionFailure("a foreground-dependent input cannot return early") }
                ) {}
            }
            precondition(owner.retainsForegroundForInput && scene.foreground == scene.target.pid,
                         "first-keydown restitution must preserve a selected foreground input mechanism")
            owner.report(to: activity)
            precondition(activity.finish(dispatch: "notDispatched", cleanup: "complete")["foregroundRestoration"]
                         as? String == "retained")
            return ["dispatch": "notDispatched", "cleanup": "complete"]
        }
        _ = try await op.execute(step: 3, payload: Data([3]), candidateID: "another-native-mechanism") {
            precondition(!owner.retainsForegroundForInput && owner.hasForegroundObligation,
                         "another candidate releases the input dependency but preserves the operation's return debt")
            precondition(owner.original == scene.original)
            return ["dispatch": "notDispatched", "cleanup": "complete"]
        }
        _ = op.finish()
    }

    @MainActor static func localPreparationDoesNotDemandIdleUser() async throws {
        let scene = Scene(), owner = scene.owner()
        owner.recordPreparation(target: scene.target)
        owner.recordRestoration(.notNeeded)
        scene.foreground = scene.user.pid
        scene.activity = [8]
        owner.recordActivation(scene.user)
        precondition(owner.permitsForegroundPreparation(),
                     "local preparation without any foreground excursion does not own the user's continued work")
        owner.recordPreparation(target: scene.target, foreground: true)
        precondition(owner.original == scene.user && owner.initialActivity == [8],
                     "the first real foreground acquisition claims the live context, not a local notification's baseline")
        scene.foreground = scene.target.pid
        let activity = scene.recorder(step: 1)
        owner.report(to: activity)
        precondition(activity.finish(dispatch: "notDispatched", cleanup: "complete")["foregroundRestoration"]
                     as? String == "retained")
        owner.stop()
    }

    @MainActor static func firstClaimUsesCurrentUserContext() async throws {
        let scene = Scene(), owner = scene.owner(), op = try operation(owner)
        scene.foreground = scene.user.pid
        scene.activity = [7]
        precondition(owner.original == nil && owner.permitsForegroundPreparation(),
                     "typing/clicking before the first SDK effect cannot refuse the first tier-two preparation")
        _ = try await op.execute(step: 1, payload: Data([1])) {
            owner.recordPreparation(target: scene.target, foreground: true)
            precondition(owner.original == scene.user && owner.initialActivity == [7],
                         "first claim uses the current user context, not operation admission's stale application")
            scene.foreground = scene.target.pid
            let activity = scene.recorder(step: 1)
            owner.report(to: activity)
            precondition(activity.finish(dispatch: "dispatched", cleanup: "complete")["foregroundRestoration"]
                         as? String == "retained")
            op.recordInputDispatch()
            return ["dispatch": "dispatched", "cleanup": "complete"]
        }
        _ = op.finish()
    }

    @MainActor static func foregroundResponsibilitySurvivesCandidates() async throws {
        let scene = Scene(target: NativeProcessIdentity.read(getpid())!)
        let shared = scene.owner(), op = try operation(shared)
        let first = try await op.execute(step: 1, payload: Data([1])) {
            precondition(NativeForegroundResponsibility.current === shared)
            shared.recordPreparation(target: scene.target)
            scene.foreground = scene.target.pid
            let outcome = await scene.restore(shared)
            precondition(outcome == .restored)
            shared.recordRestoration(outcome)
            let activity = scene.recorder(step: 1)
            shared.report(to: activity)
            return ["dispatch": "notDispatched", "cleanup": "complete",
                    "impact": activity.finish(dispatch: "notDispatched", cleanup: "complete")]
        }
        precondition((first["impact"] as! [String: Any])["foregroundRestoration"] as? String == "restored")
        precondition(!op.inputStarted && scene.activations == 1)
        let second = try await op.execute(step: 2, payload: Data([2])) {
            precondition(NativeForegroundResponsibility.current === shared && shared.original == scene.original)
            shared.requireForegroundForCandidate()
            shared.recordPreparation(target: scene.target, foreground: true)
            scene.foreground = scene.target.pid
            var events = [[String: Any]]()
            let preparationActivity = NativeInputActivity(operation: op.token, step: 2, command: "prepare", intent: "click",
                target: [:], globalInputAllowed: false,
                snapshot: { ["foregroundPID": scene.foreground as Any? ?? NSNull()] }, emit: { events.append($0) })
            await NativeInputActivity.$current.withValue(preparationActivity) {
                try! await NativeForegroundRestorationScope.perform(target: BackgroundTarget(NSRunningApplication.current),
                    onPreparation: { _ in preconditionFailure("a foreground-dependent scope cannot restore before input") }) {
                        NativeForegroundRestorationScope.current!.record(.init(kind: .windowSelection, phase: .willSend,
                            detail: "foreground menu receiver", window: nil))
                        NativeForegroundRestorationScope.current!.record(.init(kind: .windowSelection, phase: .returned,
                            detail: "foreground menu receiver", window: nil))
                    }
            }
            precondition(events.contains { $0["phase"] as? String == "foregroundRestoration"
                && $0["originalPID"] as? pid_t == scene.original.pid },
                "a later preparation scope must borrow the operation original, never recapture the target as original")
            precondition(events.contains { $0["phase"] as? String == "foregroundRestoration"
                && $0["outcome"] as? String == "retained" },
                "nested local preparation cannot return a foreground-dependent candidate before its input")
            _ = preparationActivity.finish(dispatch: "notDispatched", cleanup: "complete")
            op.recordInputDispatch()
            let activity = scene.recorder(step: 2)
            shared.report(to: activity)
            return ["dispatch": "dispatched", "cleanup": "complete",
                    "impact": activity.finish(dispatch: "dispatched", cleanup: "complete")]
        }
        precondition((second["impact"] as! [String: Any])["foregroundRestoration"] as? String == "retained",
                     "a final foreground candidate cannot reuse an earlier restored result or report notUsed")
        precondition(scene.foreground == scene.target.pid && scene.activations == 1,
                     "reporting responsibility must not activate the original app after a menu-opening click")
        scene.foreground = scene.original.pid
        let returnedWithoutRestore = scene.recorder(step: 3)
        shared.report(to: returnedWithoutRestore)
        precondition(returnedWithoutRestore.finish(dispatch: "dispatched", cleanup: "complete")["foregroundRestoration"]
                     as? String == "notNeeded", "an old candidate's restored cannot attribute a later return to the SDK")
        precondition(op.finish() == "complete" && scene.activations == 1,
                     "operation close releases observers, without a blind post-input restore")
    }

    @MainActor static func userTakeoverOnlyConstrainsRestoration() async throws {
        for physical in [false, true] {
            let scene = Scene(), owner = scene.owner(), op = try operation(owner)
            owner.recordPreparation(target: scene.target)
            scene.foreground = scene.target.pid
            _ = owner.state(target: scene.target)
            if physical { scene.activity = [1] }
            else { scene.foreground = scene.user.pid; owner.recordActivation(scene.user) }
            precondition(!owner.permitsForegroundPreparation())
            let outcome = await scene.restore(owner)
            precondition(outcome == .ownershipLost && scene.activations == 0)
            var directAX = 0
            _ = try await op.execute(step: 1, payload: Data([1])) {
                try! op.check(write: true)
                // There is no user-ownership condition on independent object
                // dispatch. It is a different dependency from foreground return.
                directAX += 1
                op.recordInputDispatch()
                return ["dispatch": "dispatched", "cleanup": "complete"]
            }
            precondition(directAX == 1)
            let activity = scene.recorder(step: 1)
            owner.report(to: activity)
            precondition(activity.finish(dispatch: "dispatched", cleanup: "complete")["foregroundRestoration"]
                         as? String == "ownershipLost")
            _ = op.finish()
        }
    }

    @MainActor static func endedTargetWindowDoesNotOwnRestoration() async throws {
        let scene = Scene(), owner = scene.owner(), op = try operation(owner)
        owner.recordPreparation(target: scene.target)
        scene.foreground = scene.target.pid
        scene.oldWindowExists = false
        let outcome = await scene.restore(owner)
        precondition(outcome == .restored && scene.activations == 1 && scene.checks == 2,
                     "process ownership restoration does not query the ended window's input boundary")
        precondition(!scene.oldWindowExists && owner.original == scene.original)
        _ = op.finish()
    }

    @MainActor static func untouchedAndUnknownAreDistinct() async throws {
        let scene = Scene(), owner = scene.owner()
        let untouched = scene.recorder(step: 1)
        owner.report(to: untouched)
        precondition(untouched.finish(dispatch: "dispatched", cleanup: "complete")["foregroundRestoration"]
                     as? String == "notUsed")
        scene.foreground = nil
        let unknown = scene.owner()
        unknown.recordPreparation(target: scene.target)
        scene.foreground = scene.target.pid
        let activity = scene.recorder(step: 2)
        unknown.report(to: activity)
        precondition(activity.finish(dispatch: "dispatched", cleanup: "complete")["foregroundRestoration"]
                     as? String == "unknown", "missing original ownership cannot be disguised as notUsed")
        owner.stop()
        unknown.stop()
    }
}
