// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import ApplicationServices
import Foundation

/// Keyboard text entry is a separate mechanism from AX text setters. These
/// facts apply only to the original object and never make AXEditable true.
struct NativeKeyboardTextEntry: Equatable {
    let role: String
    let subrole: String?

    var isPlainTextControl: Bool { [kAXTextFieldRole, kAXTextAreaRole].contains(role) }

    static func read(
        role: String?, enabled: AXRead<CFTypeRef>, field: (String) -> AXRead<CFTypeRef>
    ) -> Self? {
        guard let role, [kAXTextFieldRole, kAXTextAreaRole, kAXComboBoxRole].contains(role)
        else { return nil }
        switch enabled {
        case .value(let raw) where (raw as? Bool) == true: break
        case .unsupported, .unavailable(.noValue): break
        default: return nil
        }
        guard field("AXEditable").value as? Bool != false, field("AXReadOnly").value as? Bool != true
        else { return nil }
        let subrole: String?
        switch field(kAXSubroleAttribute) {
        case .value(let raw):
            guard let value = raw as? String else { return nil }
            subrole = value
        case .unsupported, .unavailable(.noValue): subrole = nil
        default: return nil
        }
        guard subrole != kAXSecureTextFieldSubrole else { return nil }
        return .init(role: role, subrole: subrole)
    }

    @MainActor static func read(_ element: AXUIElement) -> Self? {
        read(role: AXAccess.read(element, kAXRoleAttribute).value as? String,
             enabled: AXAccess.read(element, kAXEnabledAttribute), field: { AXAccess.read(element, $0) })
    }

    func selection(_ field: (String) -> AXRead<CFTypeRef>) -> NativeTextSelection? {
        // Scalar containers can expose a display Value without owning any
        // text receiver. Require the actual text protocol on those objects.
        if isPlainTextControl, let value = field(kAXValueAttribute).value as? String {
            guard let selection = NativeTextSelection.readBeforeRanges(field),
                selection.count == value.utf16.count, selection.contiguous, selection.location != nil
            else { return nil }
            return selection
        }
        guard let selectedText = field(kAXSelectedTextAttribute).value as? String,
            let selection = NativeTextSelection.readBeforeRanges(field, allowValueFallback: false),
            selection.contiguous, selection.location != nil, selection.length == selectedText.utf16.count
        else { return nil }
        return selection
    }

    @MainActor func selection(_ element: AXUIElement) -> NativeTextSelection? {
        selection { AXAccess.read(element, $0) }
    }
}
