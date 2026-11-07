// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices

@main enum NativeAppObserverTests {
    @MainActor static func main() async throws {
        let owner = NSRunningApplication.current
        var process = NativeProcessIdentity(pid: owner.processIdentifier, parent: 1, seconds: 1,
            microseconds: 1, executable: "observer-fixture")
        var registrations = 0, stops = 0, processReads = 0
        var kernelUnavailable = false
        var services = NativeAppObserver.Services()
        services.process = { _ in processReads += 1; return kernelUnavailable ? nil : process }
        services.register = { _, _ in
            registrations += 1
            return .init(observer: nil, notifications: ["AXWindowCreated", "AXValueChanged"], stop: { stops += 1 })
        }
        let observer = NativeAppObserver(services: services, watchTermination: false)
        let passive = try observer.status(owner: owner, application: "instance:fixture", session: "session",
            expected: process, arm: false)
        precondition(!passive.available && registrations == 0,
                     "a first visual-only read cannot implicitly initialize an AX subscription")
        precondition(observer.windowScopeEpoch(owner: owner, session: "session") == nil)
        func status() -> NativeAppObserver.Stamp {
            try! observer.status(owner: owner, application: "instance:fixture", session: "session", expected: process)
                .stamp!
        }
        let baseline = status()
        let borrowed = try observer.status(owner: owner, application: "instance:fixture", session: "session",
            expected: process, arm: false)
        precondition(borrowed.stamp == baseline && registrations == 1,
                     "a visual read after pre-write arming must retain that existing App epoch")
        precondition(status() == baseline && registrations == 1,
                     "observation and pre-write arming must share one incarnation subscription")
        let beforeEvents = processReads
        let beforeWindow = observer.windowScopeEpoch(owner: owner, session: "session")
        precondition(beforeWindow?.epoch == 0 && processReads == beforeEvents,
                     "taking the capture marker must not query the process or provider")
        observer.recordEvent(pid: process.pid, incarnation: "retired", name: "AXWindowCreated")
        observer.recordEvent(pid: process.pid, incarnation: baseline.incarnation, name: "AXUnregistered")
        precondition(processReads == beforeEvents && status() == baseline)
        observer.recordEvent(pid: process.pid, incarnation: baseline.incarnation, name: "AXWindowCreated")
        let afterWindow = observer.windowScopeEpoch(owner: owner, session: "session")
        precondition(afterWindow?.incarnation == beforeWindow?.incarnation && afterWindow?.epoch == 1,
                     "an armed window notification can trigger capture reconciliation")
        let already = try await observer.wait(owner: owner, application: baseline.application, session: "session",
            after: baseline.token, timeoutMs: 100)
        precondition(already.reason == "event" && already.events == ["AXWindowCreated"] && already.elapsedMs == 0,
                     "an event between input and post-read cannot be lost by arming too late")
        let readStart = status()
        var started = false
        let waiting = Task { @MainActor in
            started = true
            return try await observer.wait(owner: owner, application: baseline.application, session: "session",
                after: readStart.token, timeoutMs: 100)
        }
        while !started { await Task.yield() }
        let readsBeforeCallback = processReads
        observer.recordEvent(pid: process.pid, incarnation: baseline.incarnation, name: "AXValueChanged")
        precondition(processReads == readsBeforeCallback, "the callback must not query any provider/process fact")
        precondition(observer.windowScopeEpoch(owner: owner, session: "session")?.epoch == afterWindow?.epoch,
                     "a value notification alone cannot trigger window capture reconciliation")
        let changed = try await waiting.value
        precondition(changed.reason == "event" && changed.events == ["AXValueChanged"])
        let current = status()
        let timedOut = try await observer.wait(owner: owner, application: baseline.application, session: "session",
            after: current.token, timeoutMs: 2)
        precondition(timedOut.reason == "timeout" && timedOut.events.isEmpty && timedOut.stamp == current,
                     "timeout is not a change and must not increment the generation")
        started = false
        let cancelled = Task { @MainActor in
            started = true
            return try await observer.wait(owner: owner, application: baseline.application, session: "session",
                after: current.token, timeoutMs: 100)
        }
        while !started { await Task.yield() }
        cancelled.cancel()
        do { _ = try await cancelled.value; preconditionFailure("cancelled wait must unwind once") }
        catch is CancellationError {}
        precondition(status() == current, "cancellation cannot manufacture a content generation")
        kernelUnavailable = true
        let unknown = try observer.status(owner: owner, application: baseline.application, session: "session",
            expected: process)
        precondition(!unknown.available && stops == 0,
                     "an unavailable kernel read is not proof that the observed process ended")
        kernelUnavailable = false
        precondition(status() == current && registrations == 1,
                     "restoring a missing fact must reuse the same valid observer generation")
        let readsBeforeLatest = processReads
        precondition(observer.latest(baseline) == current && processReads == readsBeforeLatest,
                     "input admission must borrow the live epoch without provider/kernel queries")
        for invalid in [
            NativeAppObserver.Stamp(session: "another", application: baseline.application,
                incarnation: baseline.incarnation, token: baseline.token),
            NativeAppObserver.Stamp(session: baseline.session, application: "another",
                incarnation: baseline.incarnation, token: baseline.token),
            NativeAppObserver.Stamp(session: baseline.session, application: baseline.application,
                incarnation: "another", token: baseline.token)
        ] { precondition(observer.latest(invalid) == nil) }
        var inputSamples = 0
        let intent = try NativeIntent(["kind": "click", "parameters": ["button": "left", "count": 1]], mode: "write")
        let op = NativeOperation(timeoutMs: 1000, writable: true, intent: intent, target: [:],
            appChangeBaseline: baseline, sampleAppChange: {
                inputSamples += 1
                return observer.latest(baseline)
            }, release: {}, cancelActive: {})
        precondition(op.appChangeInputBaseline == nil && inputSamples == 0,
                     "begin/preparation must not be labeled requested input")
        op.recordInputDispatch()
        precondition(op.appChangeInputBaseline == current && inputSamples == 1)
        observer.recordEvent(pid: process.pid, incarnation: baseline.incarnation, name: "AXValueChanged")
        op.recordInputDispatch()
        precondition(op.appChangeInputBaseline == current && inputSamples == 1,
                     "later compound fragments must not swallow effects by advancing their first input baseline")
        _ = op.finish()
        process = NativeProcessIdentity(pid: process.pid, parent: 1, seconds: 2,
            microseconds: 1, executable: "observer-fixture")
        let replacement = status()
        precondition(observer.latest(baseline) == nil)
        precondition(replacement.incarnation != baseline.incarnation && registrations == 2 && stops == 1,
                     "PID reuse must release the old subscription and allocate a new incarnation")
        do {
            _ = try await observer.wait(owner: owner, application: baseline.application, session: "session",
                after: current.token, timeoutMs: 100)
            preconditionFailure("a replacement process cannot inherit the old token")
        } catch let error as DriverError { precondition(error.code == .staleTarget) }
        observer.recordEvent(pid: process.pid, incarnation: baseline.incarnation, name: "AXValueChanged")
        precondition(status() == replacement)
        observer.stop()
        observer.stop()
        precondition(stops == 2, "session shutdown must release each exact observer once")

        var unsupportedServices = services
        unsupportedServices.register = { _, _ in nil }
        let unsupported = NativeAppObserver(services: unsupportedServices, watchTermination: false)
        let noStatus = try unsupported.status(owner: owner, application: "instance:fixture", session: "session",
            expected: process)
        precondition(!noStatus.available)
        let unavailable = try await unsupported.wait(owner: owner, application: "instance:fixture", session: "session",
            after: "unknown", timeoutMs: 400)
        precondition(unavailable.reason == "unavailable" && unavailable.elapsedMs == 0 && unavailable.events.isEmpty)
        unsupported.stop()
        print("PASS App observer preserves pre-armed events, read epochs, cancellation and process lifetimes")
    }
}
