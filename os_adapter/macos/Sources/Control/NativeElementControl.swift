// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices

/// Addressed native units and their final target checks. Python selects routes.
@MainActor final class NativeElementControl {
    typealias Target = ApplicationElements.Target
    let registry: ApplicationElements
    let inspection: NativeElementInspection
    init(registry: ApplicationElements, inspection: NativeElementInspection) {
        self.registry = registry
        self.inspection = inspection
    }
    struct ActionResult {
        let verified: Bool
        let detail: String
    }

    func inputTarget(_ ref: String) throws -> Target {
        guard let target = registry.target(ref) else {
            throw DriverError("element reference expired", code: .staleTarget)
        }
        guard target.application.launchDate == target.launched else {
            throw DriverError("target process changed", code: .staleTarget)
        }
        try BackgroundTarget(target.application).validate(input: true)
        guard AXIsProcessTrusted() else {
            throw DriverError("accessibility permission denied", code: .desktopUnavailable)
        }
        try inspection.validateTargetIdentity(target)
        return target
    }

    func declaredActions(_ element: AXUIElement) throws -> [NativeAction.Item]? {
        try RequestDiagnostics.checkCancellation()
        var raw: CFArray?
        guard AXUIElementCopyActionNames(element, &raw) == .success, let rawNames = raw as? [String] else {
            return nil
        }
        return try NativeAction.catalogue(rawNames) { name in
            try RequestDiagnostics.checkCancellation()
            var description: CFString?
            AXUIElementCopyActionDescription(element, name as CFString, &description)
            return description as String?
        }
    }

    /// An explicit name needs only its own declaration. Localized aliases
    /// additionally need a complete, bounded description catalogue.
    func explicitAction(_ element: AXUIElement, request: String) throws -> NativeAction.Item? {
        try RequestDiagnostics.checkCancellation()
        var raw: CFArray?
        let status = AXUIElementCopyActionNames(element, &raw)
        let names = status == .success ? raw as? [String] : nil
        let resolved = try NativeAction.resolveExplicit(
            request: request, names: names,
            description: { name in
                var description: CFString?
                let status = AXUIElementCopyActionDescription(element, name as CFString, &description)
                try RequestDiagnostics.checkCancellation()
                return status == .success ? description as String? : nil
            }, check: RequestDiagnostics.checkCancellation)
        try RequestDiagnostics.checkCancellation()
        switch resolved {
        case .selected(let action): return action
        case .absent: return nil
        case .ambiguous:
            throw DriverError("the explicit native action is ambiguous; no requested action sent", code: .ambiguousTarget)
        case .namesUnavailable:
            throw DriverError("native action names are unavailable; no requested action sent", code: .observationIncomplete)
        case .descriptionsUnavailable:
            throw DriverError(
                "localized action descriptions are incomplete; a unique alias cannot be confirmed",
                code: .observationIncomplete)
        case .descriptionBudgetExceeded:
            throw DriverError(
                "localized action alias exceeds the description budget; use the exact native action name",
                code: .observationIncomplete)
        }
    }
}
