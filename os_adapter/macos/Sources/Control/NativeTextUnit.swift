// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices

/// Addressed text-value replacement. This unit does not find an editor, take
/// focus, synthesize keys, or retry through another input mechanism.
extension NativeElementControl {
    struct TextValueContract: Equatable {
        enum Semantics: Equatable {
            case propertyReplacement(independent: Bool)
            case providerAtomicFullReplacement(NativeProviderTextValueEvidence.Proof)
        }
        let role: String
        let subrole: String?
        let semantics: Semantics

        init(role: String, subrole: String?, independent: Bool) {
            self.role = role
            self.subrole = subrole
            semantics = .propertyReplacement(independent: independent)
        }
        init(role: String, subrole: String?, provider: NativeProviderTextValueEvidence.Proof) {
            self.role = role
            self.subrole = subrole
            semantics = .providerAtomicFullReplacement(provider)
        }
        var independent: Bool {
            switch semantics {
            case .propertyReplacement(let independent): return independent
            case .providerAtomicFullReplacement: return true
            }
        }

        var unit: String { independent ? "native_text_edit" : "native_value_replace" }
    }

    struct TextValueFacts {
        let contract: TextValueContract?
        let enabled: Bool?
        let foreground: Bool
    }

    func nativeTextValueFacts(_ ref: String) throws -> TextValueFacts {
        guard let target = registry.target(ref) else {
            throw DriverError("element reference expired", code: .staleTarget)
        }
        let background = BackgroundTarget(target.application)
        try background.validate()
        try inspection.validateTargetIdentity(target)
        let role = inspection.attribute(target.element, kAXRoleAttribute) as? String
        let subroleRead = AXAccess.read(target.element, kAXSubroleAttribute)
        let subrole: String?
        let subroleKnown: Bool
        switch subroleRead {
        case .value(let value):
            subrole = value as? String
            subroleKnown = subrole != nil
        case .unsupported, .unavailable(.noValue):
            subrole = nil
            subroleKnown = true
        default:
            subrole = nil
            subroleKnown = false
        }
        var writable = DarwinBoolean(false)
        let settable = AXUIElementIsAttributeSettable(target.element, kAXValueAttribute as CFString, &writable)
        func explicitlyUnavailable(_ capability: AXRead<Bool>) -> Bool {
            switch capability {
            case .value(false), .unsupported: return true
            default: return false
            }
        }
        let focus = AXAccess.writable(target.element, kAXFocusedAttribute)
        let independent = explicitlyUnavailable(focus)
        // A property setter does not promise normal editing/commit callbacks.
        // Value-only controls remain independent. A focusable scalar control
        // may additionally declare whole-value replacement. Shared background
        // requests can use that addressed contract before preparing keys;
        // foreground/exclusive editing keeps the callback preference. Unknown
        // capabilities, read-only editors and contextual/search setters do not
        // justify widening this contract.
        let replacement =
            subroleKnown && [kAXTextFieldRole, kAXComboBoxRole].contains(role ?? "") && subrole != "AXSearchField"
            && subrole != "AXSecureTextField" && settable == .success && writable.boolValue
            && (independent
                || focus.value == true && AXAccess.read(target.element, "AXEditable").value as? Bool != false)
            && explicitlyUnavailable(AXAccess.writable(target.element, kAXSelectedTextAttribute))
        var contract = replacement ? TextValueContract(role: role!, subrole: subrole, independent: independent) : nil
        // Keep the old independent property primitive unchanged. A separate
        // provider editing proof can strengthen only whole replacement of the
        // same addressed object; SampleNativeInputRoutes already excludes a
        // selected internal actuator before calling this method.
        if contract?.independent != true {
            let providerSubrole: NativeProviderTextValueEvidence.OptionalAttribute<String> =
                subroleKnown ? subrole.map { .value($0) } ?? .absent : .unknown
            if let provider = try NativeProviderTextValueQuery.proof(
                element: target.element, originalWindow: target.observedInteractionSurface,
                presenterPID: target.application.processIdentifier, role: role, subrole: providerSubrole,
                valueWritable: settable == .success ? writable.boolValue : nil)
            {
                contract = TextValueContract(role: role!, subrole: subrole, provider: provider)
            }
        }
        return TextValueFacts(
            contract: contract,
            enabled: (inspection.attribute(target.element, kAXEnabledAttribute) as? NSNumber)?.boolValue,
            foreground: background.isForeground)
    }

    func executeNativeTextValue(
        _ ref: String, contract: TextValueContract, text: String, onDispatch: @MainActor () throws -> Void
    ) throws -> ActionResult {
        try validateText(text)
        let target = try inputTarget(ref)
        guard try nativeTextValueFacts(ref).contract == contract else {
            throw DriverError(
                "the selected text-value replacement contract is no longer available", code: .inputUnavailable)
        }
        try inspection.requireEnabled(target.element)
        try inspection.validateTargetIdentity(target)
        try BackgroundTarget(target.application).validate(input: true)
        try RequestDiagnostics.checkCancellation()
        try onDispatch()
        let status = AXMessaging.setAttributeValue(target.element, kAXValueAttribute as CFString, text as CFString)
        guard status == .success else {
            throw DriverError(
                "native text replacement returned AX error \(status.rawValue); effect unknown, not retried",
                code: .executionUnknown, effect: .possible)
        }
        // Post-edit observation cannot erase a completed dispatch. Formatting,
        // focus loss, an unreadable value, or a destroyed editor is not a reason
        // to send the same text again. Verification is local, never 'saved'.
        do {
            try RequestDiagnostics.checkCancellation()
            try inspection.validateTargetIdentity(target)
            if let actual = inspection.attribute(target.element, kAXValueAttribute) as? String, actual == text {
                return ActionResult(verified: true, detail: "Target text value verified; submission was not requested.")
            }
        } catch { /* Preserve dispatch; the application may have changed its UI. */  }
        return ActionResult(
            verified: false, detail: "Target text-value replacement dispatched; resulting content is unconfirmed.")
    }
}
