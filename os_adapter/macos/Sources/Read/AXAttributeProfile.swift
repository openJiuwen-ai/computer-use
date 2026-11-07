// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import Foundation

/// Native attribute names are read-only query data, not an execution capability
/// allowlist. Keep request-size/name validation without restricting inspection
/// to the fields used by the default presentation profile.
enum AXAttributeProfile {
    static func attribute(_ name: String) -> Bool {
        name.hasPrefix("AX") && name.utf16.count > 2 && name.utf16.count <= 256
            && !name.unicodeScalars.contains { CharacterSet.controlCharacters.contains($0) }
    }
    static let defaultWritableFields = ["settable.AXFocused", "settable.AXValue", "settable.AXSelectedText"]
    private static let explicitSelectionFields = ["settable.AXSelectedChildren", "settable.AXSelectedRows"]

    static func writableAttribute(_ field: String) -> String? {
        guard defaultWritableFields.contains(field) || explicitSelectionFields.contains(field) else { return nil }
        return String(field.dropFirst("settable.".count))
    }

    static func field(_ name: String) -> Bool {
        attribute(name) || writableAttribute(name) != nil
    }
}
