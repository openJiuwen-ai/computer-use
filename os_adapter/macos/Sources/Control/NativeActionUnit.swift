// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices

/// A single addressed AX action, without focus/pointer/keyboard fallbacks.
/// AppKit target/action may also resolve through the selected document responder chain.
/// An observed local popup retains its own context and uses the addressed action.
extension NativeElementControl {
    struct ActionFacts {
        enum DocumentEvidence { case standard(any NativeActionDocumentInput), other, unknown }
        let role: String?
        let actions: [NativeAction.Item]?
        let enabled: Bool?
        let applicationMenuContext: Bool
        let foreground: Bool
        var focusWritable: Bool = false
        var documentEvidence = DocumentEvidence.unknown
        var hasObservedDocumentSurface = false
        var document: (any NativeActionDocumentInput)? {
            if case .standard(let value) = documentEvidence { return value }
            return nil
        }
        /// Borrowed immutable observation constraints, never action authority.
        var popupContext: NativePopupOwnerStamp<AXUIElement, NativeProcessIdentity>? = nil
        /// Current native leaf membership, separate from the observed owner.
        var popupElementWindowID: UInt32? = nil
        var qtComboChoice: NativeQtComboChoice? = nil
        var qtPopupOpener: NativeQtPopupOpener? = nil

        /// Ordinary physical windows are now resolved from the stable scope,
        /// rather than retained AXWindow ancestors. Only a document-dependent
        /// action asks for this evidence; explicit logical surfaces retain their
        /// original classification and never fall through to a parent document.
        @MainActor func resolvingDocument(
            _ resolve: () throws -> NativeFixedInputWindow?,
            subrole: (AXUIElement) -> String? = { AXAccess.read($0, kAXSubroleAttribute).value as? String }
        ) throws -> Self {
            guard case .unknown = documentEvidence, !hasObservedDocumentSurface, !applicationMenuContext,
                actions?.contains(where: { $0.name == kAXPressAction }) == true,
                let fixed = try NativeFixedInputWindow.available(resolve),
                let value = subrole(fixed.window), !value.isEmpty
            else { return self }
            var result = self
            result.documentEvidence = value == kAXStandardWindowSubrole
                ? .standard(NativeDocumentContext(application: fixed.application, window: fixed.window)) : .other
            return result
        }

        @MainActor func documentPreparation(_ action: String) throws -> NativeActionDocumentPolicy.Preparation {
            NativeActionDocumentPolicy.preparation(
                action: action, exactStandardWindow: document != nil,
                otherDocument: try action == kAXPressAction && document?.focus() == .other)
        }
    }

    func nativeActionFacts(_ ref: String, explicitRequest: String? = nil) throws -> ActionFacts {
        guard let target = registry.target(ref) else {
            throw DriverError("element reference expired", code: .staleTarget)
        }
        try BackgroundTarget(target.application).validate()
        try inspection.validateTargetIdentity(target)
        let role = inspection.attribute(target.element, kAXRoleAttribute) as? String
        let actions: [NativeAction.Item]?
        if let explicitRequest {
            actions = try explicitAction(target.element, request: explicitRequest).map { [$0] } ?? []
        } else {
            actions = try declaredActions(target.element)
        }
        var facts = ActionFacts(
            role: role, actions: actions,
            enabled: (inspection.attribute(target.element, kAXEnabledAttribute) as? NSNumber)?.boolValue,
            applicationMenuContext: registry.isApplicationMenuScope(target.scope, owner: target.application),
            foreground: BackgroundTarget(target.application).isForeground)
        if [kAXMenuButtonRole, kAXPopUpButtonRole, kAXComboBoxRole].contains(role ?? "") {
            let identifier = inspection.attribute(target.element, kAXIdentifierAttribute) as? String
            let openerActions = try explicitRequest == nil ? actions : declaredActions(target.element)
            facts.qtPopupOpener = NativeQtPopupOpener.query(role: role, identifier: identifier, actions: openerActions)
        }
        // AXPress can resolve a nil-target AppKit action through the key/main
        // document, even though the AX object itself is addressed exactly.
        // Other explicit actions retain their independent object semantics.
        if explicitRequest != nil && actions?.first?.name != kAXPressAction { return facts }
        let observedWindow = target.observedInteractionSurface
        facts.hasObservedDocumentSurface = observedWindow != nil
        if let window = observedWindow,
            let subrole = inspection.attribute(window, kAXSubroleAttribute) as? String, !subrole.isEmpty
        {
            if subrole == kAXStandardWindowSubrole {
                facts.documentEvidence = .standard(
                    NativeDocumentContext(application: target.application, window: window))
            } else {
                facts.documentEvidence = .other
            }
        }
        if explicitRequest != nil { return facts }
        facts.focusWritable = AXAccess.writable(target.element, kAXFocusedAttribute).value == true
        // Qt owns its two-path popup proof. A nil generic nested surface is
        // not absence of the original window in the retained observation path.
        facts.qtComboChoice = try NativeQtComboChoice.query(
            element: target.element, role: role, observedWindow: target.observedInteractionSurface,
            ancestors: target.ancestors, pid: target.application.processIdentifier)
        if let context = target.popupOwner, context.matchesForwardPath(target.ancestors, same: { CFEqual($0, $1) }),
            let observed = target.observedInteractionSurface,
            CFEqual(observed, context.popup)
        {
            facts.popupContext = context
            if role == "AXStaticText" || context.kind == .submenu {
                try RequestDiagnostics.checkCancellation()
                facts.popupElementWindowID = BackgroundEvents.nativeWindowID(target.element)
                try RequestDiagnostics.checkCancellation()
            }
        }
        return facts
    }

    func executeNativeAction(
        _ ref: String, action: String, description: String?, explicitRequest: String? = nil,
        onDispatch: @MainActor () throws -> Void, onReturned: @MainActor () -> Void = {}
    ) throws {
        let target = try inputTarget(ref)
        let facts = try nativeActionFacts(ref, explicitRequest: explicitRequest)
        guard !facts.applicationMenuContext else {
            throw DriverError(
                "application menu actions require the menu-context preparation unit", code: .inputUnavailable)
        }
        let matches =
            explicitRequest != nil
            ? facts.actions?.count == 1 && facts.actions?.first?.name == action
            : NativeAction.matches(facts.actions, name: action, description: description)
        guard matches else {
            throw DriverError("the selected native action is no longer declared", code: .inputUnavailable)
        }
        let background = BackgroundTarget(target.application)
        try inspection.requireEnabled(target.element)
        try inspection.validateTargetIdentity(target)
        try background.validate(input: true)
        try RequestDiagnostics.checkCancellation()
        let before =
            [kAXCheckBoxRole, kAXRadioButtonRole].contains(facts.role ?? "") && action == kAXPressAction
            ? NativeTogglePress.value(inspection.attribute(target.element, kAXValueAttribute)) : nil
        try onDispatch()  // Final window guard and barrier BEFORE the native call.
        var called = false
        let status = AXMessaging.performAction(target.element, action as CFString, onCall: { called = true })
        onReturned()
        guard status == .success else {
            if called, NativeTogglePress.mayConfirm(status),
                NativeTogglePress.canObserve(action: action, role: facts.role, before: before), let role = facts.role,
                let before
            {
                throw NativeTogglePress.Unconfirmed(role: role, before: before, status: status) { [inspection] in
                    try inspection.validateTargetIdentity(target)
                    try background.validate(input: true)
                    // A control may disable itself after handling the press.
                    // Readback needs its identity and role, not input readiness.
                    guard inspection.attribute(target.element, kAXRoleAttribute) as? String == role else { return nil }
                    return NativeTogglePress.value(inspection.attribute(target.element, kAXValueAttribute))
                }
            }
            throw DriverError(
                "native action returned AX error \(status.rawValue); effect unknown, not retried",
                code: .executionUnknown, effect: .possible)
        }
    }
}
