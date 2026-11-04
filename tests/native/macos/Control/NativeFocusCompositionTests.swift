// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices

@main enum NativeFocusCompositionTests {
    @MainActor static func main() async throws {
        try await activityPrecedesWindowPlan()
        try await optionalActivityDoesNotGate()
        try await stoppedScopeDoesNotPrepareWindow()
        try await nonKeyPanelAndCallbacks()
        try await restoredStatePrecedesConfirmation()
        try await restoredActivityIsReestablishedBeforeConfirmation()
        try await unsettledRestorationStopsBeforeConfirmation()
        print("PASS application activity precedes fresh native window planning without new readiness gates")
    }

    @MainActor static func restoredStatePrecedesConfirmation() async throws {
        let probe = FocusCompositionProbe(mode: .restoreOther)
        probe.returnForeground = true
        let outcome = try await probe.run(observeOutcome: false)
        precondition(probe.trace.firstIndex(of: "restore")! < probe.trace.firstIndex(of: "confirm")!)
        precondition(probe.trace.last == "confirm", "restoration facts must be sampled before confirmation")
        precondition(outcome.final.focusedWindow == 9 && !outcome.final.foreground)
        precondition(probe.samples == 2)
    }

    @MainActor static func restoredActivityIsReestablishedBeforeConfirmation() async throws {
        for preservesWindow in [true, false] {
            let probe = FocusCompositionProbe(mode: .restoreOther)
            probe.returnForeground = true
            probe.deactivateOnReturn = true
            probe.returnFocusedWindow = 7
            probe.preserveWindowOnReactivation = preservesWindow
            let outcome = try await probe.run(observeOutcome: false)
            precondition(probe.applicationCalls == 2 && probe.posts == 2)
            precondition(outcome.final.reportedActive == true && !outcome.final.foreground)
            precondition(outcome.final.focusedWindow == (preservesWindow ? 7 : 8),
                         "local activation can change the receiver; expose its actual window, never manufacture readiness")
            precondition(probe.selections == 1, "do not repeatedly activate and restore the window")
            precondition(probe.trace.suffix(4) == ["application", "post13", "sample", "confirm"])
            precondition(NativePointerReadiness.ready(
                kind: .click, foreground: outcome.final.foreground, windowKind: .keyRequiring,
                focus: outcome.final.focusedWindow == 7 ? .target : .otherWindow,
                backgroundPrepared: true, backgroundReportedActive: outcome.final.reportedActive) == preservesWindow)
        }
    }

    @MainActor static func unsettledRestorationStopsBeforeConfirmation() async throws {
        let probe = FocusCompositionProbe(mode: .restoreOther)
        probe.restorationUnsettled = true
        do {
            _ = try await probe.run()
            preconditionFailure("unsettled foreground return must not publish ready preparation")
        } catch let failure as DriverError {
            precondition(failure.code == .executionUnknown && failure.effect == .possible)
        }
        precondition(probe.confirms == 0)
    }

    @MainActor static func activityPrecedesWindowPlan() async throws {
        for observe in [false, true] {
            let probe = FocusCompositionProbe(mode: .restoreOther)
            probe.prepared = true
            let outcome = try await probe.run(observeOutcome: observe)
            precondition(
                probe.applicationCalls == 1 && probe.posts == 1,
                "M12 requires optional application activity before the first window-state sample")
            let expected =
                ["begin", "application", "post13", "sample", "select", "settle", "confirm"]
                + (observe ? ["sample"] : [])
            precondition(
                probe.trace == expected,
                "type13 may restore B: the real parent must freshly sample B and then select original A")
            precondition(probe.notifications == 0 && probe.selections == 1 && probe.settles == 1)
            precondition(outcome.initial.focusedWindow == 8 && outcome.initial.reportedActive == true)
            precondition(outcome.final.focusedWindow == (observe ? 7 : 8))
            precondition(probe.samples == (observe ? 2 : 1))
            precondition(probe.rememberedNeedsKey == [true] && outcome.waitEnd == .notNeeded)
        }
    }

    @MainActor static func optionalActivityDoesNotGate() async throws {
        for mode: FocusCompositionProbe.Mode in [.ignored, .unknown, .unavailable] {
            let probe = FocusCompositionProbe(mode: mode)
            _ = try await probe.run()
            precondition(probe.applicationCalls == 1 && probe.confirms == 1)
            precondition(probe.posts == (mode == .unavailable ? 0 : 1))
            precondition(
                probe.notifications == 1 && probe.selections == 0 && probe.settles == 1,
                "ignored/unavailable/unknown optional activity must continue the unchanged window/key Plan")
            precondition(probe.trace.firstIndex(of: "application")! < probe.trace.firstIndex(of: "sample")!)
            precondition(probe.active != true, "confirmation cannot manufacture an activity acknowledgement")
        }
        for mode: FocusCompositionProbe.Mode in [.foreground, .active] {
            let probe = FocusCompositionProbe(mode: mode)
            _ = try await probe.run()
            precondition(
                probe.applicationCalls == 1 && probe.posts == 0,
                "an optional stage may run while its real application unit skips the native post")
            precondition(probe.notifications == 0 && probe.selections == 0 && probe.confirms == 1)
            precondition(probe.windowWrites == 0,
                         "a positively selected target must not be activated merely to create a transport receipt")
        }
        let unchanged = FocusCompositionProbe(mode: .active)
        unchanged.prepared = true
        _ = try await unchanged.run()
        precondition(unchanged.trace == ["begin", "application", "sample", "confirm"])
        precondition(
            unchanged.settles == 0, "retain the old conditional settle; activity does not add another fixed wait")
    }

    @MainActor static func stoppedScopeDoesNotPrepareWindow() async throws {
        for failure in ["modal", "identity", "cancel"] {
            for afterPost in [false, true] {
                let probe = FocusCompositionProbe(mode: .restoreOther)
                probe.failure = failure
                probe.failAfterPost = afterPost
                do {
                    _ = try await probe.run(observeOutcome: true)
                    preconditionFailure("a changed original scope must stop preparation: \(failure)")
                } catch is CancellationError { precondition(failure == "cancel") } catch let error as DriverError {
                    precondition(error.code == (failure == "modal" ? .blockedByModal : .staleTarget))
                }
                precondition(probe.posts == (afterPost ? 1 : 0))
                precondition(probe.samples == 0 && probe.notifications == 0 && probe.selections == 0)
                precondition(
                    probe.settles == 0 && probe.confirms == 0,
                    "no later window action or confirmation may follow a failed application boundary")
                if afterPost {
                    precondition(
                        probe.ownedPost
                            && probe.effects == ["focusNotification.willSend", "focusNotification.returned"],
                        "a posted context retains its effect and lease evidence when a later check fails")
                } else {
                    precondition(probe.effects.isEmpty && !probe.ownedPost)
                }
            }
        }
    }

    @MainActor static func nonKeyPanelAndCallbacks() async throws {
        let panel = FocusCompositionProbe(mode: .panel)
        panel.prepared = true
        panel.focusedWindow = nil
        panel.active = nil
        panel.ownedPost = true
        let outcome = try await panel.run(needsKeyWindow: false, observeOutcome: true)
        precondition(panel.posts == 0 && panel.notifications == 0)
        precondition(
            panel.selections == 0 && panel.windowWrites == 0,
            "passive preparation does not require selecting a key window")
        precondition(panel.confirms == 1 && panel.rememberedNeedsKey == [false])
        precondition(panel.forwardedNeedsKey.allSatisfy { !$0 })
        precondition(outcome.initial.focusedWindow == nil && outcome.final.focusedWindow == nil)
        precondition(outcome.final.reportedActive == nil, "missing panel focus/activity cannot become a new gate")

        let callbacks = FocusCompositionProbe(mode: .restoreOther)
        callbacks.prepared = true
        callbacks.reportCleanupUnknown = true
        _ = try await callbacks.run()
        precondition(
            callbacks.legacy == ["process-local application activation notification", "native window selection"])
        precondition(
            callbacks.effects == [
                "focusNotification.willSend", "focusNotification.returned", "windowSelection.willSend",
                "windowSelection.returned"
            ])
        precondition(
            callbacks.effectWindows.count == 4 && callbacks.effectWindows[0] == nil && callbacks.effectWindows[1] == nil
                && callbacks.effectWindows[2] == 7 && callbacks.effectWindows[3] == 7)
        precondition(callbacks.cleanupUnknown == 1 && callbacks.forwardedNeedsKey.allSatisfy { $0 })
    }
}

/// Every native stage is substituted; the production parent and its real Plan
/// still determine ordering and branches. Allocated CGEvents are never posted.
@MainActor private final class FocusCompositionProbe {
    enum Mode { case restoreOther, ignored, unknown, unavailable, foreground, active, panel }
    let mode: Mode
    let application = NSRunningApplication.current
    var pid: pid_t { application.processIdentifier }
    var foregroundPID: pid_t?
    var active: Bool? = false
    var focusedWindow: CGWindowID? = 7
    var prepared = false
    var ownedPost = false
    var failure: String?
    var failAfterPost = false
    var reportCleanupUnknown = false
    var returnForeground = false
    var deactivateOnReturn = false
    var returnFocusedWindow: CGWindowID = 9
    var preserveWindowOnReactivation = false
    var restorationUnsettled = false
    var trace = [String](), legacy = [String](), effects = [String]()
    var effectWindows = [UInt32?](), forwardedNeedsKey = [Bool](), rememberedNeedsKey = [Bool]()
    var applicationCalls = 0, posts = 0, samples = 0, notifications = 0, selections = 0
    var windowWrites = 0, settles = 0, confirms = 0, cleanupUnknown = 0

    init(mode: Mode) {
        self.mode = mode
        foregroundPID = mode == .foreground ? pid : pid + 1
        if mode == .active { active = true }
    }

    private var shouldFail: Bool { failure != nil && (failAfterPost ? posts > 0 : posts == 0) }

    private func checkScope() throws {
        guard shouldFail else { return }
        if failure == "cancel" { throw CancellationError() }
        if failure == "modal" { throw DriverError("known modal", code: .blockedByModal) }
    }

    private func checkIdentity() throws {
        if shouldFail && failure == "identity" { throw DriverError("original process changed", code: .staleTarget) }
    }

    private func checkPoll() throws {
        if shouldFail && failure == "cancel" { throw CancellationError() }
        try checkIdentity()
    }

    private func sameRequest(_ request: NativeFocusRequest) {
        precondition(request.window.id == 7 && request.window.pid == pid)
        precondition(request.target.application.processIdentifier == pid && request.element == nil)
        precondition(request.window.frame == CGRect(x: 100, y: 200, width: 400, height: 200))
        forwardedNeedsKey.append(request.needsKeyWindow)
    }

    private func application(_ request: NativeFocusRequest) async throws {
        sameRequest(request)
        trace.append("application")
        applicationCalls += 1
        _ = try await NativeApplicationContextUnit.prepare(
            pid: pid, sample: { .init(foregroundPID: self.foregroundPID, reportedActive: self.active) },
            alreadyPosted: { self.ownedPost },
            make: { self.mode == .unavailable ? nil : SyntheticFocusEvents.applicationActivation() },
            post: { event in
                let native = NSEvent(cgEvent: event)!
                precondition(native.type == .appKitDefined && native.subtype.rawValue == 1)
                precondition(native.windowNumber == 0 && native.modifierFlags.isEmpty)
                self.trace.append("post13")
                self.posts += 1
                self.ownedPost = true
                if self.mode == .restoreOther {
                    self.active = true
                    if self.posts == 1 || !self.preserveWindowOnReactivation { self.focusedWindow = 8 }
                }
                if self.mode == .unknown { self.active = nil }
            },
            check: {
                try request.check()
                try self.checkIdentity()
            }, pollCheck: checkPoll, onPreparation: request.onPreparation,
            onNativePreparation: request.onNativePreparation, wait: {})
    }

    func run(needsKeyWindow: Bool = true, observeOutcome: Bool = false) async throws -> NativeFocusOutcome {
        let request = NativeFocusRequest(
            target: BackgroundTarget(application),
            window: .init(
                id: 7, pid: pid, frame: CGRect(x: 100, y: 200, width: 400, height: 200), title: "Original A",
                width: 400, height: 200), element: nil, needsKeyWindow: needsKeyWindow, check: checkScope,
            onPreparation: { self.legacy.append($0) }, onCleanupUnknown: { self.cleanupUnknown += 1 },
            onNativePreparation: { effect in
                let kind: String
                switch effect.kind {
                case .focusNotification: kind = "focusNotification"
                case .windowSelection: kind = "windowSelection"
                default: preconditionFailure("no global activation, pointer or editor preparation belongs here")
                }
                let phase: String
                switch effect.phase {
                case .willSend: phase = "willSend"
                case .returned: phase = "returned"
                }
                self.effects.append(kind + "." + phase)
                self.effectWindows.append(effect.window)
            })
        let stages = NativeFocusStages(
            begin: {
                self.sameRequest($0)
                self.trace.append("begin")
                try $0.check()
                try self.checkIdentity()
            }, applicationContext: application,
            sample: {
                self.sameRequest($0)
                try $0.check()
                try self.checkIdentity()
                self.trace.append("sample")
                self.samples += 1
                return .init(
                    foreground: self.foregroundPID == self.pid, reportedActive: self.active,
                    focusedWindow: self.focusedWindow, prepared: self.prepared)
            },
            notify: {
                self.sameRequest($0)
                try $0.check()
                try self.checkIdentity()
                self.trace.append("notify")
                self.notifications += 1
                self.recordEffect($0, kind: .focusNotification, detail: "process key-focus notification")
            },
            selectWindow: {
                self.sameRequest($0)
                try $0.check()
                try self.checkIdentity()
                self.trace.append("select")
                self.selections += 1
                if self.mode != .panel {
                    self.windowWrites += 1
                    self.focusedWindow = $0.window.id
                    self.recordEffect($0, kind: .windowSelection, detail: "native window selection")
                }
            },
            settle: {
                self.sameRequest($0)
                self.trace.append("settle")
                self.settles += 1
            },
            confirm: {
                self.sameRequest($0)
                try $0.check()
                try self.checkIdentity()
                self.trace.append("confirm")
                self.confirms += 1
                self.prepared = true
                self.rememberedNeedsKey.append($0.needsKeyWindow)
                if self.reportCleanupUnknown { $0.onCleanupUnknown() }
            }, finishPreparation: { _ in
                try NativeForegroundRestorationScope.requireSettledActivation(self.restorationUnsettled)
                guard self.returnForeground else { return false }
                self.trace.append("restore")
                self.foregroundPID = self.pid + 1
                self.focusedWindow = self.returnFocusedWindow
                if self.deactivateOnReturn { self.active = false }
                return true
            })
        return try await NativeFocusPipeline.run(request, observeOutcome: observeOutcome, stages: stages)
    }

    private func recordEffect(_ request: NativeFocusRequest, kind: NativePreparationEffect.Kind, detail: String) {
        request.onPreparation(detail)
        request.onNativePreparation(.init(kind: kind, phase: .willSend, detail: detail, window: request.window.id))
        request.onNativePreparation(.init(kind: kind, phase: .returned, detail: detail, window: request.window.id))
    }
}
