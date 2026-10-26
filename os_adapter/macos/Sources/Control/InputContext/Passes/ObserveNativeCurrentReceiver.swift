import AppKit
import ApplicationServices

/// QUERY/RESOURCE: observe current focus and its path to the addressed scope.
/// No primitive declaration, preparation, subtree census, or receiver change.
@MainActor enum ObserveNativeCurrentReceiverPass {
    static func run(_ plan: NativeInputContextPlan, access: NativeQueryAccess) throws -> [String: Any] {
        let binding = plan.query.binding
        guard binding.node != nil, !plan.query.desktop, plan.actuator == nil, plan.receiver == nil, plan.basis.isEmpty,
            ["focus", "insert", "paste", "key"].contains(plan.operation.intent)
        else {
            throw DriverError("current receiver discovery requires a container input intent", code: .invalidRequest)
        }
        // Resolve the original target outside the optional observation catch.
        // Its true identity failures still terminate this operation.
        let unit = try access.units.keyboardUnit(binding.wire, actuator: nil, receiver: nil)
        guard let scope = unit.element else {
            throw DriverError("current receiver discovery requires an element scope", code: .invalidRequest)
        }
        let application = AXUIElementCreateApplication(unit.application.processIdentifier)
        AXUIElementSetMessagingTimeout(application, 0.15)
        func focus() throws -> AXUIElement? {
            switch AXAccess.read(application, kAXFocusedUIElementAttribute) {
            case .value(let value):
                guard CFGetTypeID(value) == AXUIElementGetTypeID() else {
                    throw DriverError(
                        "the current focus report is not an accessibility object", code: .observationIncomplete)
                }
                return unsafeDowncast(value, to: AXUIElement.self)
            case .unavailable(.noValue): return nil
            default:
                throw DriverError("the application current focus report is unavailable", code: .observationIncomplete)
            }
        }
        let evidence: NativeCurrentReceiverEvidence.Observation
        do {
            evidence = try NativeCurrentReceiverEvidence.observe(
                scope: scope, focus: focus, same: CFEqual, parent: { AXAccess.element($0, kAXParentAttribute) },
                role: { AXAccess.read($0, kAXRoleAttribute).value as? String },
                locallyFocused: { AXAccess.read($0, kAXFocusedAttribute).value as? Bool },
                register: { element in
                    try access.objects.registerNativeRoot(element, app: unit.application, scope: plan.query.scope)
                }, check: RequestDiagnostics.checkCancellation)
        } catch let error as DriverError
            where [.staleTarget, .observationIncomplete, .inputUnavailable].contains(error.code)
            && (error.effect == nil || error.effect == FailureEffect.none)
        {
            try RequestDiagnostics.checkCancellation()
            evidence = .init(state: "unavailable", detail: error.description)
        }
        // Recheck the original target outside the focus observation catch.
        // Losing its identity is an operation boundary failure, not a local
        // focus gap. This query never registers dispatch contracts or binds a
        // selected recipient into the operation.
        _ = try access.scopes.inputBinding(binding.wire)
        try access.scopes.checkContainment(binding.wire, within: plan.within, op: plan.operation)
        return [
            "kind": "inputContext", "binding": binding.wire, "observed": UUID().uuidString,
            "targetLabel": "current receiver scope", "identity": "yes", "withinScope": "yes", "enabled": "unknown",
            "blockedBy": binding.blocked, "primitives": [], "within": plan.within,
            "input": [
                "foregroundSurface": NSNull(), "keyboardSurface": NSNull(), "focusedNode": NSNull(),
                "focusAncestors": [], "editor": NSNull(), "editorOwner": NSNull(), "selectionKnown": "unknown",
                "geometryCurrent": "unknown", "windowRoute": "unknown",
                "currentReceiver": [
                    "state": evidence.state, "node": evidence.node as Any? ?? NSNull(), "ancestors": evidence.ancestors,
                    "detail": evidence.detail
                ]
            ]
        ]
    }
}
