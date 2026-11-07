// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices

@MainActor final class ApplicationElements {
    struct Target {
        let application: NSRunningApplication
        let launched: Date?
        var element: AXUIElement
        let scope: String
        var role: String
        var identity: ElementIdentity
        // A nested view service can expose a public AX PID different from the
        // visible window's application. Retain that process incarnation; a
        // live native containment path, not PID equality, proves host ownership.
        var provider: NativeProcessIdentity? = nil
        var providerKnown = false
        // Native path survives presentation compression. Revalidate its edges
        // before using an ancestor as a control's interaction owner.
        var ancestors: [AXUIElement] = []
        var context: [Context] = []
        // True only for this observation's AXChildren traversal starting at a
        // window. A retained/partial ancestor list is not removal evidence.
        var contextComplete = false
        var popupOwner: NativePopupOwnerStamp<AXUIElement, NativeProcessIdentity>? = nil
        var documentLocation: NativeDocumentLocation? = nil
        // Input ownership follows this exact registered target lifetime. A new
        // observation can refine it, but cannot transfer an editor to a new cell.
        var cellEditorState = NativeCellEditorState()

        /// Original observation evidence, not a fresh parent/focus lookup.
        /// Identity validation checks these recorded roles before input uses
        /// the exact objects. A later sheet cannot adopt a document target.
        var observedInteractionSurface: AXUIElement? {
            let roles = [kAXWindowRole, kAXSheetRole, "AXPopover"]
            if roles.contains(identity.attributes[kAXRoleAttribute] ?? "") { return element }
            // A qualified popup's recorded attachment is a distinct input
            // mechanism, not the generic physical-window lifetime check.
            if let popupOwner { return popupOwner.popup }
            // The fixed physical window is resolved by NativeScopeResolver.
            // Only a nested logical surface needs this additional anchor;
            // keeping an old AXWindow proxy here duplicates its lifetime gate.
            return context.last {
                [kAXSheetRole, "AXPopover"].contains($0.identity.attributes[kAXRoleAttribute] ?? "")
            }?.native.element
        }
    }
    struct Identity: Hashable {
        let pid: pid_t
        let launched: Date?
        let element: AXUIElement?
        var nativeWindow: UInt32? = nil
        var incarnation: String? = nil
        var scope: String? = nil

        static func == (lhs: Identity, rhs: Identity) -> Bool {
            lhs.pid == rhs.pid && lhs.launched == rhs.launched && lhs.nativeWindow == rhs.nativeWindow
                && lhs.incarnation == rhs.incarnation && lhs.scope == rhs.scope
                && {
                    if let a = lhs.element, let b = rhs.element { return CFEqual(a, b) }
                    return lhs.element == nil && rhs.element == nil
                }()
        }
        func hash(into hasher: inout Hasher) {
            hasher.combine(pid)
            hasher.combine(launched)
            hasher.combine(nativeWindow)
            hasher.combine(incarnation)
            hasher.combine(scope)
            if let element { hasher.combine(CFHash(element)) }
        }
    }
    struct Context: Equatable {
        let native: Identity
        let identity: ElementIdentity
        var documentLocation: NativeDocumentLocation? = nil
    }
    struct Evidence: Equatable {
        let identity: ElementIdentity
        let provider: NativeProcessIdentity?
        let context: [Context]
        var providerKnown = false
    }
    // Tokens capture immutable text but resolve actions through confirmed native lifetimes.
    private let namespace = String(UInt64.random(in: .min ... .max), radix: 36)
    private lazy var elements = ObservedElements<Identity, Evidence, Target>(
        namespace: namespace, reconcile: Self.reconcile)
    private let references = ElementReferences<Identity>()
    // A physical window can appear through more than one logical application.
    // Keep its public window number, but never share action/observation state
    // between those owners. NSRunningApplication equality includes its lifetime.
    private struct ScopeIdentity: Hashable {
        let owner: NSRunningApplication
        let window: String?
    }
    private let scopeReferences = ElementReferences<ScopeIdentity>()

    /// Reconcile evidence for the same native key, never search for a substitute
    /// object. Missing attributes retain historical constraints, not a claim of
    /// current input readiness; each native unit still rechecks live evidence.
    nonisolated private static func reconcile(
        _ previous: Evidence, _ previousTarget: Target, _ next: Evidence, _ newTarget: Target
    ) -> (Evidence, Target)? {
        guard let identity = previous.identity.refining(with: next.identity) else {
            diagnose(["event": "element_identity_reconcile", "subject": "element", "scope": previousTarget.scope,
                      "fields": previous.identity.changedFields(in: .init(values: next.identity.attributes, absent: next.identity.absent))])
            return nil
        }
        guard let contexts = reconcileContext(previous.context, next.context, completePath: newTarget.contextComplete)
        else { return nil }
        let oldKnown = previous.providerKnown || previous.provider != nil
        let newKnown = next.providerKnown || next.provider != nil
        guard !oldKnown || !newKnown || previous.provider == next.provider else {
            diagnose(["event": "element_identity_reconcile", "subject": "provider", "scope": previousTarget.scope])
            return nil
        }
        let provider = newKnown ? next.provider : previous.provider
        var target = newTarget
        let currentBoundaries = Set(Self.boundaryContext(next.context).map(\.native))
        if !newTarget.contextComplete, contexts.contains(where: { !currentBoundaries.contains($0.native) }) {
            // Equal-size partial paths can still omit an original host window
            // while adding a new nested owner. Retained boundary evidence,
            // rather than the old/new counts, identifies this missing path.
            target.ancestors = previousTarget.ancestors
        }
        // A later observation may create a new lifetime, never fill an old
        // reference with newly discovered ownership or change its old owner.
        if let nextOwner = newTarget.popupOwner {
            guard let oldOwner = previousTarget.popupOwner, oldOwner.matches(nextOwner, same: { CFEqual($0, $1) })
            else { return nil }
        }
        target.popupOwner = previousTarget.popupOwner
        target.cellEditorState = previousTarget.cellEditorState
        target.identity = identity
        target.role = identity.attributes["AXRole"] ?? newTarget.role
        target.provider = provider
        target.providerKnown = oldKnown || newKnown
        target.context = contexts
        return (
            Evidence(identity: identity, provider: provider, context: contexts, providerKnown: target.providerKnown),
            target
        )
    }

    /// Only ownership evidence propagates into descendant lifetimes. This
    /// projection is shared by observation reconciliation and final validation;
    /// a named layout wrapper must not disappear from one and survive in the other.
    nonisolated static func boundaryContext(_ context: [Context]) -> [Context] {
        var inMenu = false
        return context.compactMap { item in
            inMenu = inMenu || item.identity.attributes["AXRole"] == "AXMenuBar"
            guard let identity = item.identity.inheritedBoundary(inMenu: inMenu) else { return nil }
            return Context(native: item.native, identity: identity, documentLocation: item.documentLocation)
        }
    }

    /// A partial traversal may omit old evidence; omission is not a different
    /// owner. Refine matching boundaries and retain omitted constraints. Only
    /// a complete path can prove that the original boundary was removed; a
    /// different native owner in partial evidence may be another nested owner.
    nonisolated static func reconcileContext(
        _ previous: [Context], _ next: [Context], completePath: Bool = false
    ) -> [Context]? {
        var merged = boundaryContext(previous)
        let currentPath = boundaryContext(next)
        if completePath {
            // Unlike a partial subtree, a path actually traversed from the
            // fixed window supplies evidence of removal. This also catches a
            // document owner replaced by a different role, without treating
            // extra nested owners or layout wrappers as contradictions.
            for old in merged where old.identity.attributes["AXRole"] != "AXWindow" {
                guard currentPath.contains(where: { $0.native == old.native }) else {
                    diagnose(["event": "element_identity_reconcile", "subject": "context",
                              "role": old.identity.attributes["AXRole"] ?? "unknown",
                              "reason": "ownership boundary removed from complete observed path"])
                    return nil
                }
            }
        }
        var ordered = [Context]()
        for (position, current) in currentPath.enumerated() {
            let physicalWindow = current.identity.attributes["AXRole"] == "AXWindow"
            if let index = merged.firstIndex(where: {
                $0.native == current.native || physicalWindow && $0.identity.attributes["AXRole"] == "AXWindow"
            }) {
                // This reconciliation is only called for the same native leaf
                // key and fixed scope. WindowRegistry/NativeScopeResolver own
                // the physical window lifetime; its AX proxy may be refreshed.
                guard let identity = merged[index].identity.refining(with: current.identity) else {
                    diagnose(["event": "element_identity_reconcile", "subject": "context", "role": current.identity.attributes["AXRole"] ?? "unknown",
                              "fields": merged[index].identity.changedFields(in: .init(values: current.identity.attributes, absent: current.identity.absent))])
                    return nil
                }
                let refined = Context(
                    native: current.native, identity: identity,
                    documentLocation: current.documentLocation ?? merged[index].documentLocation)
                merged[index] = refined
                ordered.append(refined)
            } else {
                // Even a subtree can prove that a new owner precedes an
                // already retained inner owner. Preserve that local order
                // without treating other omitted boundaries as removed.
                let nextKnown = currentPath.dropFirst(position + 1).first { next in
                    merged.contains { $0.native == next.native }
                }
                let insertion = nextKnown.flatMap { next in merged.firstIndex { $0.native == next.native } }
                merged.insert(current, at: insertion ?? merged.count)
                ordered.append(current)
            }
        }
        // A complete path also supplies the current containment order. Merely
        // appending newly observed outer owners would make them look innermost
        // to observedInteractionSurface, despite the original leaf surviving.
        if completePath { return ordered }
        // A partial path also establishes the relative order of the boundaries
        // it actually contains. Refill only their slots in current order;
        // omitted owners stay retained and omission never proves removal.
        let observed = Set(ordered.map(\.native))
        var current = ordered.makeIterator()
        var retained = merged.map { observed.contains($0.native) ? current.next() ?? $0 : $0 }
        retained.append(contentsOf: current)
        return retained
    }

    func invalidate(pid: pid_t, scope: String? = nil) {
        elements.invalidate { $0.application.processIdentifier == pid && (scope == nil || $0.scope == scope) }
    }

    func observationScope(_ app: NSRunningApplication, window: AXUIElement?, reference: String? = nil) -> String {
        let windowRef =
            reference
            ?? window.map {
                references.reference(for: Identity(pid: app.processIdentifier, launched: app.launchDate, element: $0))
            }
        return "\(namespace):scope" + scopeReferences.reference(for: ScopeIdentity(owner: app, window: windowRef))
    }

    /// The application menu is registered independently of window content.
    /// A menu-bar role in a window's observation does not change its scope.
    func isApplicationMenuScope(_ scope: String, owner: NSRunningApplication) -> Bool {
        scopeReferences.knownReference(for: ScopeIdentity(owner: owner, window: nil))
            .map { "\(namespace):scope" + $0 == scope } ?? false
    }

    func windowReference(_ window: NativeWindows.Window, element: AXUIElement? = nil) -> String {
        windowReference(
            nativeID: window.mapping.id, process: window.process, owner: window.application, element: element)
    }

    /// Identity continuity does not prove current visibility, geometry, or
    /// input readiness; those stay with their native query owners.
    func windowReference(
        nativeID: CGWindowID, process: NativeProcessIdentity, owner: NSRunningApplication, element: AXUIElement?
    ) -> String {
        let aliases =
            element.map {
                [
                    Identity(
                        pid: process.pid, launched: owner.launchDate, element: $0,
                        incarnation: process.incarnation)
                ]
            } ?? []
        return references.reference(
            for: Identity(
                pid: process.pid, launched: nil, element: nil, nativeWindow: nativeID,
                incarnation: process.incarnation), aliases: aliases)
    }

    /// Rejoin an already-proven physical identity when the AX root is newer
    /// than this collection's onscreen projection. Never allocate a physical
    /// key without the native evidence that also owns its lifetime cleanup.
    func knownWindowReference(
        nativeID: CGWindowID, process: NativeProcessIdentity, owner: NSRunningApplication, element: AXUIElement
    ) -> String? {
        guard references.knownReference(for: Identity(
            pid: process.pid, launched: nil, element: nil, nativeWindow: nativeID,
            incarnation: process.incarnation)) != nil
        else { return nil }
        return windowReference(nativeID: nativeID, process: process, owner: owner, element: element)
    }

    func retireWindowReference(_ window: NativeWindows.Window) {
        references.retire(Identity(
            pid: window.process.pid, launched: nil, element: nil, nativeWindow: window.mapping.id,
            incarnation: window.process.incarnation))
    }

    func nativeScope(_ window: NativeWindows.Window, owner: NSRunningApplication) -> String {
        observationScope(owner, window: nil, reference: windowReference(window))
    }

    // The registry owns lifetimes and captured text. Collaborators
    // receive value copies or explicit operations, never mutable dictionaries.
    func target(_ token: String) -> Target? { elements.target(token) }
    func text(_ token: String) -> ObservedElements<Identity, Evidence, Target>.TextSnapshot? { elements.text(token) }
    func observe(_ key: Identity, evidence: Evidence, value: Target) -> String {
        var evidence = evidence
        evidence.providerKnown = evidence.providerKnown || value.providerKnown || value.provider != nil
        return elements.observe(key, evidence: evidence, value: value)
    }
    func observed(_ key: Identity) -> (id: String, value: Target)? { elements.observed(key) }
    func retain(_ ref: String) -> (() -> Void)? { elements.retain(ref) }
    static func childContext(_ target: Target) -> [Context] {
        boundaryContext(target.context + [
            Context(
                native: Identity(
                    pid: target.application.processIdentifier, launched: target.launched,
                        element: target.element), identity: target.identity, documentLocation: target.documentLocation)
        ])
    }
    func bind(_ lifetime: String, text: String?) -> String? { elements.bind(lifetime, text: text) }
    func invalidate(where predicate: (Target) -> Bool) { elements.invalidate(where: predicate) }
    func knownReference(for identity: Identity) -> String? { references.knownReference(for: identity) }
    func reference(for identity: Identity, aliases: [Identity] = []) -> String {
        references.reference(for: identity, aliases: aliases)
    }
}
