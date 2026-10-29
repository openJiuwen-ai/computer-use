// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import ApplicationServices

/// Provider-owned text positions are opaque. Do not reinterpret them as UTF-16
/// offsets or use displayed text equality to prove a complete selection.
struct NativeTextMarkerSelection {
    static let selectedAttribute = "AXSelectedTextMarkerRange"
    static let startAttribute = "AXStartTextMarker"
    static let endAttribute = "AXEndTextMarker"
    let range: AXTextMarkerRange
    let full: Bool
    var empty: Bool { CFEqual(AXTextMarkerRangeCopyStartMarker(range), AXTextMarkerRangeCopyEndMarker(range)) }

    static func read(whole: () -> AXRead<CFTypeRef>, selected: () -> AXRead<CFTypeRef>, valid: (AXTextMarker) -> Bool)
        -> Self?
    {
        func typed(_ raw: CFTypeRef?) -> AXTextMarkerRange? {
            guard let raw, CFGetTypeID(raw) == AXTextMarkerRangeGetTypeID() else { return nil }
            return unsafeDowncast(raw, to: AXTextMarkerRange.self)
        }
        guard let range = typed(whole().value), valid(AXTextMarkerRangeCopyStartMarker(range)),
            valid(AXTextMarkerRangeCopyEndMarker(range))
        else { return nil }
        let full: Bool
        switch selected() {
        case .value(let raw):
            guard let selection = typed(raw) else { return nil }
            full = CFEqual(range, selection)
        case .unsupported, .unavailable(.noValue): full = false
        default: return nil
        }
        // A changing native extent cannot establish a complete selection.
        guard let after = typed(whole().value), CFEqual(range, after) else { return nil }
        return Self(range: range, full: full)
    }
}
