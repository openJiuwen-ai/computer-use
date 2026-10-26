import AppKit
import ApplicationServices

/// QUERY-only provider adapter. No tree discovery, preparation or setter. Each
/// call proves the original object through its live parent chain and current
/// range bounds; there is no cross-call capability cache or foreground need.
@MainActor enum NativeProviderTextValueQuery {
    typealias Evidence = NativeProviderTextValueEvidence

    private static func string(_ element: AXUIElement, _ key: String) -> String? {
        guard let value = AXAccess.read(element, key).value, CFGetTypeID(value) == CFStringGetTypeID() else {
            return nil
        }
        return value as? String
    }
    private static func boolean(_ element: AXUIElement, _ key: String) -> Evidence.OptionalAttribute<Bool> {
        switch AXAccess.read(element, key) {
        case .value(let value):
            guard CFGetTypeID(value) == CFBooleanGetTypeID(), let boolean = value as? Bool else { return .unknown }
            return .value(boolean)
        case .unsupported, .unavailable(.noValue): return .absent
        default: return .unknown
        }
    }
    private static func publicPID(_ element: AXUIElement) -> pid_t? {
        var pid: pid_t = 0
        return AXUIElementGetPid(element, &pid) == .success && pid > 0 ? pid : nil
    }
    static func proof(
        element: AXUIElement, originalWindow: AXUIElement?, presenterPID: pid_t, role: String?,
        subrole: Evidence.OptionalAttribute<String>, valueWritable: Bool?
    ) throws -> Evidence.Proof? {
        guard role == kAXTextFieldRole, valueWritable == true else { return nil }
        switch subrole {
        case .absent, .value(""): break
        default: return nil
        }
        try RequestDiagnostics.checkCancellation()
        // Ordinary native controls must not inherit Web ancestry/geometry
        // prerequisites merely because their old property offer is sampled.
        guard let providerNode = string(element, "ChromeAXNodeId"), Evidence.nodeID(providerNode) != nil else {
            return nil
        }
        guard let originalWindow, string(originalWindow, kAXRoleAttribute) == kAXWindowRole,
            let windowID = BackgroundEvents.nativeWindowID(originalWindow), windowID != 0,
            publicPID(originalWindow) == presenterPID, publicPID(element) == presenterPID,
            BackgroundEvents.accessibilityProcessID(element) == presenterPID,
            BackgroundEvents.nativeWindowID(element) == windowID, let value = string(element, kAXValueAttribute),
            let scalar = value.unicodeScalars.first
        else { return nil }
        // A non-BMP first scalar needs both UTF16 code units. Do not manufacture
        // a character to classify an empty field and do not cache prior proof.
        let rangeLength = String(scalar).utf16.count
        var cursor = element, seen = [AXUIElement](), area: AXUIElement?
        var reachedWindow = false, ambiguousWebArea = false
        for _ in 0..<24 {
            try RequestDiagnostics.checkCancellation()
            guard !seen.contains(where: { CFEqual($0, cursor) }), publicPID(cursor) == presenterPID,
                let currentRole = string(cursor, kAXRoleAttribute)
            else { return nil }
            seen.append(cursor)
            if CFEqual(cursor, originalWindow) {
                reachedWindow = true
                break
            }
            // No unrelated sheet/window/popover can be treated as a transparent
            // ancestor to the original document's atomic-field offer.
            if [kAXWindowRole, kAXSheetRole, "AXPopover"].contains(currentRole) { return nil }
            if currentRole == "AXWebArea" {
                if area != nil { ambiguousWebArea = true }
                area = cursor
            }
            guard let parent = AXAccess.element(cursor, kAXParentAttribute) else { return nil }
            cursor = parent
        }
        guard reachedWindow, !ambiguousWebArea, let area, BackgroundEvents.nativeWindowID(area) == windowID,
            BackgroundEvents.accessibilityProcessID(area) == presenterPID, let areaNode = string(area, "ChromeAXNodeId")
        else { return nil }
        try RequestDiagnostics.checkCancellation()
        var range = CFRange(location: 0, length: rangeLength)
        guard let argument = AXValueCreate(.cfRange, &range) else { return nil }
        var raw: CFTypeRef?
        let status = AXUIElementCopyParameterizedAttributeValue(
            element, kAXBoundsForRangeParameterizedAttribute as CFString, argument, &raw)
        let bounds: Evidence.Bounds
        if status != .success {
            bounds = .unavailable(status.rawValue)
        } else if let raw, CFGetTypeID(raw) == AXValueGetTypeID() {
            let typed = unsafeDowncast(raw, to: AXValue.self)
            var rect = CGRect.zero
            if AXValueGetType(typed) == .cgRect, AXValueGetValue(typed, .cgRect, &rect) {
                bounds = .rectangle(rect)
            } else {
                bounds = .malformed
            }
        } else {
            bounds = .malformed
        }
        let enabled: Bool?
        if case .value(let value) = boolean(element, kAXEnabledAttribute) { enabled = value } else { enabled = nil }
        let facts = Evidence.Facts(
            role: role, subrole: subrole, valueWritable: valueWritable, enabled: enabled,
            editable: boolean(element, "AXEditable"), valueUTF16Length: value.utf16.count, rangeLocation: 0,
            rangeLength: rangeLength, bounds: bounds,
            scope: Evidence.ScopeFacts(
                presenterPID: presenterPID, elementPublicPID: publicPID(element),
                elementActualPID: BackgroundEvents.accessibilityProcessID(element),
                windowPublicPID: publicPID(originalWindow), webAreaPublicPID: publicPID(area),
                webAreaActualPID: BackgroundEvents.accessibilityProcessID(area), originalWindowID: windowID,
                elementWindowID: BackgroundEvents.nativeWindowID(element),
                webAreaWindowID: BackgroundEvents.nativeWindowID(area), reachedOriginalWindow: reachedWindow,
                exactlyOneWebArea: !ambiguousWebArea, elementProviderNodeID: providerNode,
                webAreaProviderNodeID: areaNode))
        try RequestDiagnostics.checkCancellation()
        return Evidence.prove(facts)
    }
}
