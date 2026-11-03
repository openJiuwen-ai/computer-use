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
        var document: (any NativeActionDocumentInput)? {
            if case .standard(let value) = documentEvidence { return value }
            return nil
        }
        /// Borrowed immutable observation constraints, never action authority.
        var popupContext: NativePopupOwnerStamp<AXUIElement, NativeProcessIdentity>? = nil
        /// Current native leaf membership, separate from the observed owner.
        var popupElementWindowID: UInt32? = nil
        var qtComboChoice: NativeQtComboChoice? = nil

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
            applicationMenuContext: [kAXMenuBarRole, kAXMenuBarItemRole].contains(role ?? "")
                || target.ancestors.contains { inspection.attribute($0, kAXRoleAttribute) as? String == kAXMenuBarRole },
            foreground: BackgroundTarget(target.application).isForeground)
        // AXPress can resolve a nil-target AppKit action through the key/main
        // document, even though the AX object itself is addressed exactly.
        // Other explicit actions retain their independent object semantics.
        if explicitRequest != nil && actions?.first?.name != kAXPressAction { return facts }
        if let window = target.observedInteractionSurface,
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
        facts.qtComboChoice = try NativeQtComboChoice.query(
            element: target.element, role: role, observedWindow: target.observedInteractionSurface,
            ancestors: target.ancestors, pid: target.application.processIdentifier)
        if let context = target.popupOwner, target.ancestors.count == 2, CFEqual(target.ancestors[0], context.popup),
            CFEqual(target.ancestors[1], context.list), let observed = target.observedInteractionSurface,
            CFEqual(observed, context.popup)
        {
            facts.popupContext = context
            if role == "AXStaticText" {
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
