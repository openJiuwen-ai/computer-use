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
            case geckoAtomicFullReplacement(NativeGeckoTextProvider.Proof)
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
        init(role: String, subrole: String?, gecko: NativeGeckoTextProvider.Proof) {
            self.role = role
            self.subrole = subrole
            semantics = .geckoAtomicFullReplacement(gecko)
        }
        var independent: Bool {
            switch semantics {
            case .propertyReplacement(let independent): return independent
            case .providerAtomicFullReplacement, .geckoAtomicFullReplacement: return true
            }
        }

        var unit: String { independent ? "native_text_edit" : "native_value_replace" }
    }

    struct TextValueFacts {
        let contract: TextValueContract?
        let enabled: Bool?
        let foreground: Bool
        let geckoProvider: NativeGeckoTextProvider.Proof?
    }

    func nativeTextValueFacts(
        _ ref: String, replacementText: String, originalWindow: () throws -> AXUIElement?
    ) throws -> TextValueFacts {
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
        // may additionally declare whole-value replacement. Background requests
        // can use that addressed contract before preparing keys; an already
        // prepared editor keeps the ordinary callback preference. Unknown
        // capabilities, read-only editors and contextual/search setters do not
        // justify widening this contract.
        let replacement =
            subroleKnown && [kAXTextFieldRole, kAXComboBoxRole].contains(role ?? "") && subrole != "AXSearchField"
            && subrole != "AXSecureTextField" && settable == .success && writable.boolValue
            && (independent
                || focus.value == true && AXAccess.read(target.element, "AXEditable").value as? Bool != false)
            && explicitlyUnavailable(AXAccess.writable(target.element, kAXSelectedTextAttribute))
        var contract = replacement ? TextValueContract(role: role!, subrole: subrole, independent: independent) : nil
        let enabled = (inspection.attribute(target.element, kAXEnabledAttribute) as? NSNumber)?.boolValue
        let geckoProvider =
            try !replacementText.isEmpty && role == kAXTextFieldRole && subrole == kAXSearchFieldSubrole
            ? NativeGeckoTextProvider.prove(
                element: target.element, presenterPID: target.application.processIdentifier,
                originalWindow: { try target.observedInteractionSurface ?? originalWindow() })
            : nil
        // Keep the old independent property primitive unchanged. A separate
        // provider editing proof can strengthen only whole replacement of the
        // same addressed object; SampleNativeInputRoutes already excludes a
        // selected internal actuator before calling this method.
        if contract?.independent != true {
            let providerSubrole: NativeProviderTextValueEvidence.OptionalAttribute<String> =
                subroleKnown ? subrole.map { .value($0) } ?? .absent : .unknown
            if let proof = geckoProvider, let role,
                enabled == true, settable == .success, writable.boolValue,
                AXAccess.read(target.element, "AXEditable").value as? Bool != false,
                AXAccess.read(target.element, "AXReadOnly").value as? Bool != true,
                NativeGeckoTextProvider.needsAtomicReplacement(
                    text: replacementText, value: AXAccess.read(target.element, kAXValueAttribute).value as? String),
                NativeGeckoTextProvider.focused(proof)
            {
                // Gecko AXValue performs one editor replacement. It can
                // establish a selection and focus, so only an already
                // confirmed receiver gets this independent offer.
                contract = TextValueContract(role: role, subrole: subrole, gecko: proof)
            } else if let provider = try NativeProviderTextValueQuery.proof(
                element: target.element,
                originalWindow: { try target.observedInteractionSurface ?? originalWindow() },
                presenterPID: target.application.processIdentifier, role: role, subrole: providerSubrole,
                valueWritable: settable == .success ? writable.boolValue : nil)
            {
                contract = TextValueContract(role: role!, subrole: subrole, provider: provider)
            }
        }
        return TextValueFacts(
            contract: contract, enabled: enabled, foreground: background.isForeground, geckoProvider: geckoProvider)
    }

    func executeNativeTextValue(
        _ ref: String, contract: TextValueContract, text: String, onDispatch: @MainActor () throws -> Void,
        originalWindow: () throws -> AXUIElement?
    ) throws -> ActionResult {
        try validateText(text)
        let target = try inputTarget(ref)
        let refreshed = try nativeTextValueFacts(ref, replacementText: text, originalWindow: originalWindow).contract
        guard refreshed == contract else {
            if case .geckoAtomicFullReplacement = contract.semantics {
                throw NativeRouteUnavailable(description: "the original atomic text replacement is unavailable")
            }
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
            verified: false, detail: "Target text-value replacement dispatched.")
    }
}
