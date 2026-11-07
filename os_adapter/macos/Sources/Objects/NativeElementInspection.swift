// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices

/// Native identity and provider checks; no editor discovery or input routing.
@MainActor final class NativeElementInspection {
    typealias Target = ApplicationElements.Target
    private let registry: ApplicationElements
    init(registry: ApplicationElements) { self.registry = registry }

    func attribute(_ element: AXUIElement, _ name: String) -> CFTypeRef? { AXAccess.read(element, name).value }

    func opensMenu(_ element: AXUIElement, withinBudget: () -> Bool = { true }) -> Bool {
        func attribute(_ object: AXUIElement, _ key: String) -> CFTypeRef? {
            guard withinBudget() else { return nil }
            return self.attribute(object, key)
        }
        let role = attribute(element, kAXRoleAttribute) as? String
        if role == kAXMenuBarItemRole { return true }
        return role == kAXMenuItemRole
            && (attribute(element, kAXChildrenAttribute) as? [AXUIElement] ?? []).contains {
                attribute($0, kAXRoleAttribute) as? String == kAXMenuRole
            }
    }

    func providerFacts(_ element: AXUIElement, app: NSRunningApplication) -> (
        identity: NativeProcessIdentity?, known: Bool
    ) {
        var pid: pid_t = 0
        guard AXUIElementGetPid(element, &pid) == .success else { return (nil, false) }
        if pid == app.processIdentifier { return (nil, true) }
        let identity = NativeProcessIdentity.read(pid)
        return (identity, identity != nil)
    }

    private func validateTargetProvider(_ target: Target) throws {
        var pid: pid_t = 0
        let status = AXUIElementGetPid(target.element, &pid)
        guard status == .success else {
            throw DriverError(
                "element provider identity is unavailable (AX status \(status.rawValue)); no input was dispatched",
                code: status == .invalidUIElement ? .staleTarget : .observationIncomplete)
        }
        if pid == target.application.processIdentifier, target.provider == nil { return }
        guard let provider = target.provider else {
            throw DriverError(
                "element provider identity cannot be confirmed",
                code: target.providerKnown ? .staleTarget : .observationIncomplete)
        }
        guard provider.pid == pid else { throw DriverError("element provider changed", code: .staleTarget) }
        guard let current = NativeProcessIdentity.read(pid) else {
            throw DriverError(
                "element provider lifetime cannot be read",
                code: NativeProcessIdentity.isGone(pid) ? .staleTarget : .observationIncomplete)
        }
        guard current == provider else { throw DriverError("element provider lifetime changed", code: .staleTarget) }
        guard
            let window = target.ancestors.reversed().first(where: { ancestor in
                var owner: pid_t = 0
                return [kAXWindowRole, kAXSheetRole].contains(attribute(ancestor, kAXRoleAttribute) as? String ?? "")
                    && AXUIElementGetPid(ancestor, &owner) == .success && owner == target.application.processIdentifier
            }),
            let current = WindowGeometry.owningWindow(
                target.element, pid: target.application.processIdentifier, in: window)
        else {
            throw DriverError(
                "element provider attachment to its observed window is unavailable", code: .observationIncomplete)
        }
        guard CFEqual(current, window) else {
            throw DriverError("element provider moved to another window", code: .staleTarget)
        }
    }

    /// An invalid ancestor proxy does not prove the retained leaf was destroyed.
    /// Keep the reference while reporting incomplete boundary evidence; a known
    /// document/record contradiction is different and may retire that binding.
    nonisolated static func identityCheck(
        _ identity: ElementIdentity, evidence: AXRead<ElementIdentity.Facts>, isTarget: Bool
    ) -> (state: ElementIdentity.Check, detail: String) {
        switch evidence {
        case .invalid:
            return (isTarget ? .changed : .unavailable, "AX status \(AXError.invalidUIElement.rawValue)")
        case .unavailable(let error):
            return (.unavailable, "AX status \(error.rawValue)")
        case .unsupported:
            return (.unavailable, "identity query unsupported")
        case .value(let facts):
            let state = identity.check(facts)
            let fields = state == .changed ? identity.changedFields(in: facts) : identity.unavailableFields(in: facts)
            return (state, "fields=\(fields.joined(separator: ","))")
        }
    }

    func validateTargetIdentity(_ target: Target) throws {
        let metric = RequestDiagnostics.metrics?.start(.targetValidation)
        defer { metric?.end() }
        do {
            try validateTargetProvider(target)
        } catch let error as DriverError where error.code == .staleTarget {
            // A confirmed provider/leaf lifetime conflict has the same one-way
            // retirement semantics as an identity-field conflict below.
            // Incomplete provider evidence deliberately leaves the handle live.
            registry.invalidate { $0.scope == target.scope && CFEqual($0.element, target.element) }
            throw error
        }
        func check(
            _ element: AXUIElement, _ identity: ElementIdentity, subject: String, isTarget: Bool,
            location: NativeDocumentLocation? = nil
        ) throws -> (state: ElementIdentity.Check, detail: String)
        {
            let evidence = AXIdentity.read(element, keys: identity.attributes.keys.sorted())
            let result = Self.identityCheck(identity, evidence: evidence, isTarget: isTarget)
            if result.state == .changed, let location, let facts = evidence.value, identity.locationChange(facts) {
                let deadline = ProcessInfo.processInfo.systemUptime + 0.15
                if try location.confirms(target: target.element, deadline: deadline) {
                    return (.matched, "original document location advanced")
                }
                if ProcessInfo.processInfo.systemUptime >= deadline {
                    return (.unavailable, "document location continuity was not sampled before the input deadline")
                }
            }
            let detail = "\(subject) role=\(identity.attributes[kAXRoleAttribute] ?? "unknown") \(result.detail)"
            if result.state != .matched {
                diagnose(["event": "element_identity_check", "subject": subject,
                          "state": String(describing: result.state), "scope": target.scope, "detail": detail])
            }
            return (result.state, detail)
        }
        let boundaries = ApplicationElements.boundaryContext(target.context)
        let nearestDocument = boundaries.lastIndex { $0.identity.attributes[kAXRoleAttribute] == "AXWebArea" }
        let checks =
            [
                try check(
                    target.element, target.identity, subject: "element", isTarget: true,
                    location: target.documentLocation)
            ]
            + (try boundaries.enumerated().map { index, context in
                if context.identity.attributes[kAXRoleAttribute] == kAXWindowRole {
                    // The input scope checks the original physical window and
                    // membership. Do not let a dead AXWindow proxy invalidate a
                    // live leaf. A declared document constraint still applies,
                    // sampled from the leaf's current window where available.
                    guard context.identity.attributes[kAXDocumentAttribute] != nil else {
                        return (.matched, "context[\(index)] window lifetime is checked by the input scope")
                    }
                    let current = AXAccess.element(target.element, kAXWindowAttribute) ?? context.native.element
                    return try current.map {
                        try check(
                            $0, context.identity, subject: "context[\(index)] current window document", isTarget: false)
                    } ?? (.unavailable, "context[\(index)] window document cannot be obtained")
                }
                return try context.native.element.map {
                    try check(
                        $0, context.identity, subject: "context[\(index)]", isTarget: false,
                        location: index == nearestDocument ? context.documentLocation : nil)
                } ?? (.unavailable, "context[\(index)] has no native reference")
            })
        if let changed = checks.first(where: { $0.state == .changed }) {
            // Do not revive a handle after a known identity/structural
            // contradiction. Re-observation establishes a new lifetime.
            registry.invalidate { $0.scope == target.scope && CFEqual($0.element, target.element) }
            throw DriverError(
                "element identity boundary changed: \(changed.detail); the original reference was retired",
                code: .staleTarget)
        }
        if let unavailable = checks.first(where: { $0.state == .unavailable }) {
            throw DriverError(
                "element identity boundary is temporarily unconfirmed: \(unavailable.detail); the reference is retained",
                code: .observationIncomplete,
                next: "use the attached observation; the retained target has not been replaced or retired")
        }
    }

    func requireEnabled(_ element: AXUIElement, preparedMenu: Bool = false) throws {
        guard (attribute(element, kAXEnabledAttribute) as? NSNumber)?.boolValue != false else {
            if preparedMenu {
                throw DriverError(
                    "menu command remains disabled after target preparation; requested command was not dispatched",
                    code: .inputUnavailable,
                    next: """
                        change the required document state or choose another command; repeating this request \
                        alone will not enable it
                        """)
            }
            throw DriverError("element is disabled", code: .inputUnavailable)
        }
    }
}
