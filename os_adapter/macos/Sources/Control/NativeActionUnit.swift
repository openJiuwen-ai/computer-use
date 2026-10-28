// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices

/// A single addressed AX action, without focus/pointer/keyboard fallbacks.
/// Only application-menu-bar commands depend on the selected document receiver.
/// An observed local popup retains its own context and uses the addressed action.
extension NativeElementControl {
    struct ActionFacts {
        enum DocumentEvidence { case standard(NativeDocumentContext), other, unknown }
        let role: String?
        let actions: [NativeAction.Item]?
        let enabled: Bool?
        let applicationMenuContext: Bool
        let foreground: Bool
        var focusWritable: Bool = false
        var documentEvidence = DocumentEvidence.unknown
        var document: NativeDocumentContext? {
            if case .standard(let value) = documentEvidence { return value }
            return nil
        }
        var webArea = NativeActionDocumentPolicy.WebArea.unknown

        @MainActor func allowsAction(_ action: String) -> Bool {
            NativeActionAdmission.allows(
                action: action, role: role, focusWritable: focusWritable, foreground: foreground,
                exclusive: BackgroundTarget.isExclusive)
        }

        @MainActor func documentPreparation(_ action: String, retainedForegroundRequirement: Bool = false) throws
            -> NativeActionDocumentPolicy.Preparation
        {
            NativeActionDocumentPolicy.preparation(
                action: action, exactStandardWindow: document != nil,
                otherDocument: try action == kAXPressAction && document?.focus() == .other,
                confirmedWebArea: webArea == .confirmed, foreground: foreground,
                exclusive: BackgroundTarget.isExclusive, retainedForegroundRequirement: retainedForegroundRequirement)
        }

        @MainActor func documentForeground(_ action: String) -> NativeClickContract.DocumentForeground {
            let kind: NativeActionDocumentPolicy.DocumentKind
            switch documentEvidence {
            case .standard: kind = .standard
            case .other: kind = .other
            case .unknown: kind = .unknown
            }
            return NativeActionDocumentPolicy.foregroundEvidence(
                action: action, document: kind, web: webArea, foreground: foreground,
                exclusive: BackgroundTarget.isExclusive)
        }
    }

    func nativeActionFacts(_ ref: String) throws -> ActionFacts {
        guard let target = registry.target(ref) else {
            throw DriverError("element reference expired", code: .staleTarget)
        }
        try BackgroundTarget(target.application).validate()
        try inspection.validateTargetIdentity(target)
        let role = inspection.attribute(target.element, kAXRoleAttribute) as? String
        var facts = ActionFacts(
            role: role, actions: try declaredActions(target.element),
            enabled: (inspection.attribute(target.element, kAXEnabledAttribute) as? NSNumber)?.boolValue,
            applicationMenuContext: [kAXMenuBarRole, kAXMenuBarItemRole].contains(role ?? "")
                || target.ancestors.contains { inspection.attribute($0, kAXRoleAttribute) as? String == kAXMenuBarRole },
            foreground: BackgroundTarget(target.application).isForeground,
            focusWritable: AXAccess.writable(target.element, kAXFocusedAttribute).value == true)
        if let window = target.observedInteractionSurface,
            let subrole = inspection.attribute(window, kAXSubroleAttribute) as? String, !subrole.isEmpty
        {
            if subrole == kAXStandardWindowSubrole {
                facts.documentEvidence = .standard(
                    NativeDocumentContext(application: target.application, window: window))
                if BackgroundTarget.isExclusive, facts.actions?.contains(where: { $0.name == kAXPressAction }) == true {
                    facts.webArea = try NativeActionDocumentPolicy.classifyWebArea(
                        element: target.element, window: window, same: { CFEqual($0, $1) },
                        role: { AXAccess.read($0, kAXRoleAttribute).value as? String },
                        parent: { AXAccess.element($0, kAXParentAttribute) },
                        check: RequestDiagnostics.checkCancellation)
                }
            } else {
                facts.documentEvidence = .other
            }
        }
        return facts
    }

    func executeNativeAction(
        _ ref: String, action: String, description: String?, onDispatch: @MainActor () throws -> Void,
        onReturned: @MainActor () -> Void = {}
    ) throws {
        let target = try inputTarget(ref)
        let facts = try nativeActionFacts(ref)
        guard !facts.applicationMenuContext else {
            throw DriverError(
                "application menu actions require the menu-context preparation unit", code: .inputUnavailable)
        }
        guard NativeAction.matches(facts.actions, name: action, description: description) else {
            throw DriverError("the selected native action is no longer declared", code: .inputUnavailable)
        }
        let background = BackgroundTarget(target.application)
        if !facts.allowsAction(action) {
            throw DriverError("raising a background window requires exclusive mode", code: .modeUnsupported)
        }
        if !background.allowsMenuPopup, [kAXPressAction, kAXPickAction].contains(action),
            inspection.opensMenu(target.element)
        {
            throw DriverError("opening an application menu is unavailable in this desktop mode", code: .modeUnsupported)
        }
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
