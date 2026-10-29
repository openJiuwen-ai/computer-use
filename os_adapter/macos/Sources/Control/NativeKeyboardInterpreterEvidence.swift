// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import Foundation

/// A positive local-Web observation selects a transport; missing parent data
/// before any Web evidence must not disable ordinary native background input.
enum NativeKeyboardInterpreterEvidence {
    enum Kind: String, Equatable { case noObservedWeb, localWeb, remoteWeb, observedWebOwnershipUnknown }
    enum ParentQueryFailure: Equatable { case noValue, unsupported, cannotComplete }
    enum Parent<Node> { case value(Node), queryUnavailable(ParentQueryFailure), unavailable }
    enum ContinuationScope: Equatable { case original, other, unavailable }
    struct RemoteContinuation<Node> {
        let editor: Node
        let area: Node
        let presenter: Int32
        let actualPID: Int32
        let parentFailure: ParentQueryFailure
    }
    struct RemoteScopeFacts {
        let editorScope: ContinuationScope
        let areaScope: ContinuationScope
        let editorPublicPID: Int32?
        let areaPublicPID: Int32?
        let editorActualPID: Int32?
        let areaActualPID: Int32?
    }
    enum Uncertainty: Equatable { case queryUnavailable, identityConflict }
    struct Observation<Node> {
        let kind: Kind
        let area: Node?
        var uncertainty: Uncertainty? = nil
        var complete = false
        var remoteContinuation: RemoteContinuation<Node>? = nil
    }
    enum Policy: Equatable { case existing, webObserved, localWebNoProcess }

    /// Immutable primitive policy retains positive evidence across query/execute.
    static func retain(_ previous: Policy, observed: Kind) -> Policy {
        if previous == .localWebNoProcess || observed == .localWeb { return .localWebNoProcess }
        if previous == .webObserved || observed != .noObservedWeb { return .webObserved }
        return .existing
    }
    static func applying<Node>(_ policy: Policy, to observation: Observation<Node>) -> Observation<Node> {
        if policy == .localWebNoProcess && observation.kind != .localWeb
            || policy == .webObserved && observation.kind == .noObservedWeb
        {
            return .init(
                kind: .observedWebOwnershipUnknown, area: observation.area,
                uncertainty: observation.uncertainty ?? (observation.complete ? .identityConflict : .queryUnavailable),
                complete: observation.complete)
        }
        return observation
    }

    /// PURE: retain the old remote-host classification only for the typed
    /// post-WebArea parent-query failure. This supplies no input permission;
    /// the selected process recipient still executes every existing guard.
    static func supplementRemote<Node>(_ observation: Observation<Node>, policy: Policy, facts: RemoteScopeFacts)
        -> Observation<Node>
    {
        guard policy != .localWebNoProcess, observation.kind == .observedWebOwnershipUnknown,
            observation.uncertainty == .queryUnavailable, let partial = observation.remoteContinuation,
            partial.presenter > 0, partial.actualPID > 0, partial.actualPID != partial.presenter
        else { return observation }
        let p = partial.presenter, r = partial.actualPID
        if facts.editorScope == .other || facts.areaScope == .other || facts.editorPublicPID.map({ $0 != p }) == true
            || facts.areaPublicPID.map({ $0 != p }) == true || facts.editorActualPID.map({ $0 != r }) == true
            || facts.areaActualPID.map({ $0 != r }) == true
        {
            return .init(kind: .observedWebOwnershipUnknown, area: partial.area, uncertainty: .identityConflict)
        }
        guard facts.editorScope == .original, facts.areaScope == .original, facts.editorPublicPID == p,
            facts.areaPublicPID == p, facts.editorActualPID == r, facts.areaActualPID == r
        else { return observation }
        return .init(kind: .remoteWeb, area: partial.area, complete: true)
    }

    /// PURE: follow only the actual parent chain of this call's editor.
    static func classify<Node>(
        editor: Node?, window: Node, presenter: Int32, limit: Int = 32, same: (Node, Node) -> Bool,
        role: (Node) -> String?, owner: (Node) -> Int32?, actualPID: (Node) -> Int32?, parent: (Node) -> Parent<Node>,
        check: () throws -> Void = {}
    ) throws -> Observation<Node> {
        guard let editor else { return .init(kind: .noObservedWeb, area: nil) }
        var current = editor
        var seen = [Node](), area: Node?, actual: Int32?, editorActual: Int32?, areaCount = 0, ownershipKnown = true,
            conflict = false
        func unavailable() -> Observation<Node> {
            .init(
                kind: area == nil ? .noObservedWeb : .observedWebOwnershipUnknown, area: area,
                uncertainty: area == nil ? nil : (conflict ? .identityConflict : .queryUnavailable))
        }
        while seen.count < limit {
            try check()
            guard !seen.contains(where: { same($0, current) }) else { return unavailable() }
            seen.append(current)
            if let owner = owner(current) {
                if owner != presenter {
                    ownershipKnown = false
                    conflict = true
                }
            } else {
                ownershipKnown = false
            }
            if same(current, window) {
                guard let area else { return .init(kind: .noObservedWeb, area: nil, complete: true) }
                guard ownershipKnown, let actual, actual > 0 else {
                    return .init(
                        kind: .observedWebOwnershipUnknown, area: area,
                        uncertainty: conflict ? .identityConflict : .queryUnavailable)
                }
                return .init(kind: actual == presenter ? .localWeb : .remoteWeb, area: area, complete: true)
            }
            let currentRole = role(current)
            if currentRole == "AXWebArea" {
                areaCount += 1
                if area == nil {
                    area = current
                    actual = actualPID(current)
                    editorActual = actualPID(editor)
                    if let actual, let editorActual {
                        if actual != editorActual {
                            ownershipKnown = false
                            conflict = true
                        }
                    } else {
                        ownershipKnown = false
                    }
                }
            }
            guard currentRole != nil else { return unavailable() }
            if ["AXApplication", "AXWindow", "AXSheet"].contains(currentRole!) {
                if area != nil { conflict = true }
                return unavailable()
            }
            switch parent(current) {
            case .value(let next): current = next
            case .queryUnavailable(let failure):
                var result = unavailable()
                if presenter > 0, areaCount == 1, let area, let actual, actual > 0, actual != presenter,
                    editorActual == actual, ownershipKnown, !conflict
                {
                    result.remoteContinuation = .init(
                        editor: editor, area: area, presenter: presenter, actualPID: actual, parentFailure: failure)
                }
                return result
            case .unavailable: return unavailable()
            }
        }
        return unavailable()
    }

    /// Textless clear/commands never acquire a Unicode-only qualification.
    static func needsTextTransport(kind: String, text: String?) -> Bool {
        ["insert", "replace"].contains(kind) && text?.isEmpty == false
    }
}
