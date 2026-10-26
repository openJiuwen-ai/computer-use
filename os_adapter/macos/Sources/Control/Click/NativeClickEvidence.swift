import Foundation

/// One attempt's typed evidence. NativeOperation owns admission/step history;
/// this object only accumulates the effects of that admitted attempt.
@MainActor final class NativeClickEvidence {
    struct Preparation {
        let kind: String
        let effect: String
        var dispatch: String
        var observations: [String]
        var wait = "notNeeded"
        var continuation: String { effect == "contextOnly" ? "withinScope" : "sameCandidate" }
    }
    let target: [String: Any]
    private(set) var routeFamily: String
    private(set) var preparation = [Preparation]()
    private var transportKind = "none"
    private var entered = false
    private var returned = false
    private var continuation = "stop"
    private var pendingPreparation: Int?

    init(target: [String: Any], family: String = "macos.unresolved") {
        self.target = target
        routeFamily = family
    }
    func select(_ family: String) { routeFamily = family }
    var hasContentPossible: Bool { preparation.contains { $0.effect != "contextOnly" } }
    var permitsReselection: Bool { !hasContentPossible && pendingPreparation == nil }

    func record(_ event: NativePreparationEffect) {
        let kind: String, effect: String
        switch event.kind {
        case .focusNotification:
            kind = "focusNotification"
            effect = "contextOnly"
        case .windowSelection:
            kind = "windowSelection"
            effect = "contextOnly"
        case .foregroundActivation:
            kind = "activation"
            effect = "contextOnly"
        case .menuContext:
            kind = "activation"
            effect = "contentPossible"
        }
        if event.phase == .willSend {
            preparation.append(.init(kind: kind, effect: effect, dispatch: "unknown", observations: [event.detail]))
            pendingPreparation = preparation.count - 1
        } else if let index = pendingPreparation, preparation[index].kind == kind {
            preparation[index].dispatch = "dispatched"
            pendingPreparation = nil
        }
    }

    func observe(_ outcome: NativeFocusOutcome) {
        let active = outcome.final.reportedActive.map { $0 ? "yes" : "no" } ?? "unknown"
        let observations = [
            "applicationActive=\(active)", "systemForeground=\(outcome.final.foreground)",
            "focusedWindow=\(outcome.final.focusedWindow.map(String.init) ?? "unknown")"
        ]
        let wait: String
        switch outcome.waitEnd {
        case .notNeeded: wait = "notNeeded"
        case .activeObserved: wait = "satisfied"
        case .providerUnknown: wait = "unavailable"
        case .exhausted: wait = "exhausted"
        }
        if preparation.isEmpty {
            preparation.append(
                .init(
                    kind: "focusNotification", effect: "contextOnly", dispatch: "notDispatched",
                    observations: observations, wait: wait))
        } else {
            preparation[preparation.count - 1].observations += observations
            preparation[preparation.count - 1].wait = wait
        }
    }

    func markCall(_ kind: String) {
        transportKind = kind
        entered = true
        returned = false
    }
    func recordWait(_ outcome: NativePreparationWait) {
        guard !preparation.isEmpty else { return }
        preparation[preparation.count - 1].wait = outcome.rawValue
    }
    func markReturned() { returned = true }
    func complete() { continuation = "stop" }
    func fail(dispatch: String, cleanup: String, retryReason: String?) {
        if hasContentPossible || pendingPreparation != nil {
            continuation = "observe"
        } else if dispatch == "notDispatched", cleanup == "complete", let retryReason {
            continuation = retryReason == "preconditionChanged" ? "sameCandidateRefresh" : "reselectWithinScope"
        } else {
            continuation = dispatch == "notDispatched" ? "stop" : "observe"
        }
    }

    var wire: [String: Any] {
        let effects: [[String: Any]] = preparation.map { item in
            [
                "kind": item.kind, "scope": target, "dispatch": item.dispatch, "effect": item.effect,
                "continuation": continuation == "observe" ? "observe" : item.continuation,
                "observations": item.observations, "wait": item.wait
            ]
        }
        return [
            "preparationEvidence": effects,
            "transportEvidence": [
                "kind": transportKind, "callEntered": entered, "callReturned": returned, "submitted": NSNull(),
                "expected": NSNull()
            ] as [String: Any],
            "continuation": ["kind": continuation, "target": target, "routeFamily": routeFamily] as [String: Any]
        ]
    }
}
