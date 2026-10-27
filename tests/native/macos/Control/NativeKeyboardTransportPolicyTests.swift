import Foundation

@main struct NativeKeyboardTransportPolicyTests {
    enum Stop: Error { case deadline }
    @MainActor static func main() async throws {
        typealias E = NativeKeyboardInterpreterEvidence
        let parents = [1: 2, 2: 3, 3: 4]
        func classify(
            roles: [Int: String] = [1: "AXTextField", 2: "AXGroup", 3: "AXWebArea", 4: "AXWindow"],
            links: [Int: Int] = parents, owners: [Int: Int32] = [:], actual: Int32? = 7, limit: Int = 32
        ) -> E.Observation<Int> {
            try! E.classify(
                editor: 1, window: 4, presenter: 7, limit: limit, same: ==, role: { roles[$0] },
                owner: { owners[$0] ?? 7 }, actualPID: { _ in actual },
                parent: { links[$0].map(E.Parent.value) ?? .unavailable })
        }
        precondition(classify().kind == .localWeb)
        precondition(classify(actual: 8).kind == .remoteWeb)
        precondition(classify(actual: nil).kind == .observedWebOwnershipUnknown)
        precondition(classify(links: [1: 2, 2: 3]).kind == .observedWebOwnershipUnknown)
        precondition(classify(links: [1: 2, 2: 3, 3: 2]).kind == .observedWebOwnershipUnknown)
        precondition(classify(owners: [2: 99]).kind == .observedWebOwnershipUnknown)
        precondition(
            classify(roles: [1: "AXTextField", 2: "AXGroup", 3: "AXGroup", 4: "AXWindow"]).kind == .noObservedWeb)
        precondition(classify(links: [1: 2]).kind == .noObservedWeb)
        precondition(
            classify(roles: [1: "AXTextField", 2: "AXGroup", 3: "AXWebArea", 4: "AXWindow"], limit: 3).kind
                == .observedWebOwnershipUnknown)
        let badOther = try E.classify(
            editor: 1, window: 4, presenter: 7, same: ==,
            role: { [1: "AXTextField", 2: "AXWebArea", 3: "AXWindow"][$0] }, owner: { _ in 7 }, actualPID: { _ in 7 },
            parent: { [1: 2, 2: 3][$0].map(E.Parent.value) ?? .unavailable })
        precondition(badOther.kind == .observedWebOwnershipUnknown)
        let mismatchedEditor = try E.classify(
            editor: 1, window: 4, presenter: 7, same: ==,
            role: { [1: "AXTextField", 2: "AXWebArea", 4: "AXWindow"][$0] }, owner: { _ in 7 },
            actualPID: { $0 == 1 ? 8 : 7 }, parent: { [1: 2, 2: 4][$0].map(E.Parent.value) ?? .unavailable })
        precondition(
            mismatchedEditor.kind == .observedWebOwnershipUnknown && mismatchedEditor.uncertainty == .identityConflict)
        let immutable = E.retain(.existing, observed: .localWeb)
        precondition(E.retain(immutable, observed: .noObservedWeb) == .localWebNoProcess)
        let failedBeforeWeb = E.applying(immutable, to: E.Observation<Int>(kind: .noObservedWeb, area: nil))
        precondition(
            failedBeforeWeb.kind == .observedWebOwnershipUnknown && failedBeforeWeb.uncertainty == .queryUnavailable)
        precondition(
            E.applying(immutable, to: E.Observation<Int>(kind: .remoteWeb, area: 2, complete: true)).uncertainty
                == .identityConflict)
        let webUnknown = E.retain(.existing, observed: .observedWebOwnershipUnknown)
        precondition(
            E.applying(webUnknown, to: E.Observation<Int>(kind: .noObservedWeb, area: nil)).kind
                == .observedWebOwnershipUnknown)
        // Proposed compatibility edge: real remote editor -> WebArea, whose
        // Parent query is unavailable, while both old explicit window proofs
        // remain exact. No local or conflicting chain borrows this fallback.
        func remotePartial(
            failure: E.ParentQueryFailure? = .noValue, missingAt: Int = 3, links: [Int: Int] = [1: 2, 2: 3],
            roles: [Int: String] = [1: "AXTextField", 2: "AXGroup", 3: "AXWebArea", 4: "AXWindow", 5: "AXGroup"],
            actual: Int32 = 8, editorActual: Int32? = 8, foreignOwner: Int? = nil, limit: Int = 32
        ) -> E.Observation<Int> {
            try! E.classify(
                editor: 1, window: 4, presenter: 7, limit: limit, same: ==, role: { roles[$0] },
                owner: { $0 == foreignOwner ? 99 : 7 }, actualPID: { $0 == 1 ? editorActual : actual },
                parent: { node in
                    if let next = links[node] { return .value(next) }
                    if node == missingAt, let failure { return .queryUnavailable(failure) }
                    return .unavailable
                })
        }
        func remoteScopes(
            editor: E.ContinuationScope = .original, area: E.ContinuationScope = .original, editorPublic: Int32? = 7,
            areaPublic: Int32? = 7, editorActual: Int32? = 8, areaActual: Int32? = 8
        ) -> E.RemoteScopeFacts {
            .init(
                editorScope: editor, areaScope: area, editorPublicPID: editorPublic, areaPublicPID: areaPublic,
                editorActualPID: editorActual, areaActualPID: areaActual)
        }
        let incompleteRemote = remotePartial()
        precondition(
            incompleteRemote.kind == .observedWebOwnershipUnknown && incompleteRemote.remoteContinuation != nil)
        for failure in [E.ParentQueryFailure.noValue, .unsupported, .cannotComplete] {
            let observed = remotePartial(failure: failure)
            let resumed = E.supplementRemote(observed, policy: .webObserved, facts: remoteScopes())
            precondition(resumed.kind == .remoteWeb && resumed.area == 3 && resumed.complete)
        }
        precondition(E.supplementRemote(incompleteRemote, policy: .existing, facts: remoteScopes()).kind == .remoteWeb)
        let retainedLocal = E.supplementRemote(incompleteRemote, policy: .localWebNoProcess, facts: remoteScopes())
        precondition(retainedLocal.kind == .observedWebOwnershipUnknown)
        precondition(E.applying(.localWebNoProcess, to: retainedLocal).kind != .remoteWeb)
        var alreadyConflicting = incompleteRemote
        alreadyConflicting.uncertainty = .identityConflict
        precondition(
            E.supplementRemote(alreadyConflicting, policy: .webObserved, facts: remoteScopes()).uncertainty
                == .identityConflict)
        let localWithStalePartial = E.Observation<Int>(
            kind: .localWeb, area: 3, remoteContinuation: incompleteRemote.remoteContinuation)
        precondition(
            E.supplementRemote(localWithStalePartial, policy: .existing, facts: remoteScopes()).kind == .localWeb)
        for uncertain in [
            remoteScopes(editor: .unavailable), remoteScopes(area: .unavailable), remoteScopes(editorPublic: nil),
            remoteScopes(areaPublic: nil), remoteScopes(editorActual: nil), remoteScopes(areaActual: nil)
        ] {
            precondition(
                E.supplementRemote(incompleteRemote, policy: .webObserved, facts: uncertain).kind
                    == .observedWebOwnershipUnknown)
        }
        for conflicting in [
            remoteScopes(editor: .other), remoteScopes(area: .other), remoteScopes(editorPublic: 99),
            remoteScopes(areaPublic: 99), remoteScopes(editorActual: 9), remoteScopes(areaActual: 9)
        ] {
            let rejected = E.supplementRemote(incompleteRemote, policy: .webObserved, facts: conflicting)
            precondition(rejected.kind == .observedWebOwnershipUnknown && rejected.uncertainty == .identityConflict)
        }
        let noSupplements = [
            remotePartial(actual: 7, editorActual: 7),  // Local cannot borrow it.
            remotePartial(editorActual: 9), remotePartial(editorActual: nil), remotePartial(foreignOwner: 2),
            remotePartial(failure: nil),  // Includes invalid/malformed parent.
            remotePartial(missingAt: 2, links: [1: 2]),  // Before any WebArea.
            remotePartial(links: [1: 2, 2: 3, 3: 2]),  // Known cycle.
            remotePartial(links: [1: 2, 2: 3, 3: 5], limit: 3),  // Limit, not a query failure.
            // Unknown role.
            remotePartial(links: [1: 2, 2: 3, 3: 5], roles: [1: "AXTextField", 2: "AXGroup", 3: "AXWebArea"]),
            // Another window.
            remotePartial(
                links: [1: 2, 2: 3, 3: 5], roles: [1: "AXTextField", 2: "AXGroup", 3: "AXWebArea", 5: "AXWindow"]),
            remotePartial(roles: [1: "AXTextField", 2: "AXWebArea", 3: "AXWebArea", 4: "AXWindow"])  // Multiple areas.
        ]
        for observed in noSupplements {
            precondition(observed.remoteContinuation == nil)
            precondition(E.supplementRemote(observed, policy: .webObserved, facts: remoteScopes()).kind != .remoteWeb)
        }
        var focusIsOriginal = true, finalSamples = 0
        let lateFocus = try NativeKeyboardReadinessGate.queryThenReceiver(
            mechanism: {
                focusIsOriginal = false
                return .ready
            },
            receiver: {
                finalSamples += 1
                return focusIsOriginal
            }, validate: { $0 ? .ready : .conflicting("late other-control focus") })
        if case .conflicting = lateFocus {} else { preconditionFailure("receiver must be fresh after mechanism query") }
        precondition(finalSamples == 1)
        _ = try NativeKeyboardReadinessGate.queryThenReceiver(
            mechanism: { .unavailable("parent cannotComplete") },
            receiver: {
                finalSamples += 1
                return true
            }, validate: { _ in .ready })
        precondition(finalSamples == 1)
        for kind in ["key", "focus", "paste"] { precondition(!E.needsTextTransport(kind: kind, text: "hello")) }
        for kind in ["insert", "replace"] {
            precondition(E.needsTextTransport(kind: kind, text: "hello"))
            precondition(!E.needsTextTransport(kind: kind, text: ""))
            precondition(!E.needsTextTransport(kind: kind, text: nil))
        }
        typealias P = NativeKeyboardTransportPolicy
        for interpreter in [E.Kind.noObservedWeb, .remoteWeb] {
            precondition(
                P.select(usesText: true, interpreter: interpreter, applicationForeground: false, window: .other)
                    == .process)
        }
        precondition(
            P.select(usesText: true, interpreter: .localWeb, applicationForeground: false, window: .target)
                == .foregroundPreparation)
        precondition(
            P.select(usesText: true, interpreter: .localWeb, applicationForeground: true, window: .other)
                == .foregroundPreparation)
        precondition(
            P.select(usesText: true, interpreter: .localWeb, applicationForeground: true, window: .hidden)
                == .foregroundPreparation)
        precondition(
            P.select(usesText: true, interpreter: .localWeb, applicationForeground: true, window: .target)
                == .scopedForeground)
        precondition(
            P.select(usesText: true, interpreter: .localWeb, applicationForeground: true, window: .unavailable)
                == .scopedForeground)
        precondition(
            P.select(
                usesText: true, interpreter: .observedWebOwnershipUnknown, applicationForeground: true, window: .target)
                == .unavailable)
        precondition(
            P.select(
                usesText: false, interpreter: .observedWebOwnershipUnknown, applicationForeground: false, window: .other
            ) == .process)

        for read in [P.WindowRead.unsupported, .noValue, .cannotComplete] {
            precondition(
                P.exactReceiverSubstitutesWindow(read, scope: true, editor: true, owner: true, locallyFocused: true))
            precondition(
                !P.exactReceiverSubstitutesWindow(read, scope: true, editor: false, owner: true, locallyFocused: true))
            precondition(
                !P.exactReceiverSubstitutesWindow(read, scope: true, editor: true, owner: false, locallyFocused: true))
            precondition(
                !P.exactReceiverSubstitutesWindow(read, scope: true, editor: true, owner: true, locallyFocused: false))
        }
        for read in [P.WindowRead.other, .invalid, .wrongType, .failed] {
            precondition(
                !P.exactReceiverSubstitutesWindow(read, scope: true, editor: true, owner: true, locallyFocused: true))
        }
        // One call owns the retry allowance. A sent prefix is never replayed.
        var events = [String](), samples = 0, now = 0.0
        let gate = NativeKeyboardReadinessGate(waits: 2)
        let pair = PairedInput<String>(post: { events.append($0) }, canRelease: { true }, settle: {})
        try await pair.run {
            pair.press("A-down", release: "A-up")
            pair.release()
            try await gate.confirm(
                check: {},
                sample: {
                    samples += 1
                    return samples == 1 ? .unavailable("AX cannotComplete") : .ready
                }, wait: { now += $0 })
            pair.press("B-down", release: "B-up")
            pair.release()
        }
        precondition(events == ["A-down", "A-up", "B-down", "B-up"] && gate.remainingWaits == 1)
        do {
            try await gate.confirm(check: {}, sample: { .unavailable("still noValue") }, wait: { now += $0 })
            preconditionFailure("shared finite allowance must expire")
        } catch NativeKeyboardReadinessGate.Failure.unavailable {}
        precondition(gate.remainingWaits == 0)

        // Known other window never waits and never reaches another key/down.
        events = []
        samples = 0
        do {
            let otherGate = NativeKeyboardReadinessGate()
            let otherPair = PairedInput<String>(post: { events.append($0) }, canRelease: { true }, settle: {})
            try await otherPair.run {
                otherPair.press("A-down", release: "A-up")
                otherPair.release()
                try await otherGate.confirm(
                    check: {}, sample: { .conflicting("other window") },
                    wait: { _ in preconditionFailure("known conflict cannot wait") })
                otherPair.press("wrong-down", release: "wrong-up")
            }
            preconditionFailure("known conflict must stop")
        } catch NativeKeyboardReadinessGate.Failure.conflicting {}
        precondition(events == ["A-down", "A-up"])

        // Exhaustion/cancellation/deadline preserve one captured cleanup release.
        for failure in 0..<3 {
            events = []
            now = 0
            let stopGate = NativeKeyboardReadinessGate(waits: 1)
            let stopPair = PairedInput<String>(post: { events.append($0) }, canRelease: { true }, settle: {})
            do {
                try await stopPair.run {
                    stopPair.press("held-down", release: "captured-up")
                    try await stopGate.confirm(
                        check: { if failure == 2 && now > 0 { throw Stop.deadline } },
                        sample: { .unavailable("temporary") },
                        wait: {
                            if failure == 1 { throw CancellationError() }
                            now += $0
                        })
                    stopPair.press("unexpected-down", release: "unexpected-up")
                }
                preconditionFailure("must stop")
            } catch {}
            precondition(events == ["held-down", "captured-up"])
        }
        print(
            """
            PASS scoped keyboard: native/remote/local/positive-unknown, same-App other-window, textless \
            clear, shared readonly budget, prefix/no-replay/conflict/cancel/deadline captured release
            """)
    }
}
