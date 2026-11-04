// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import Foundation
import CoreGraphics

/// A single-window query can omit an existing ordered-out/auxiliary window.
/// Absence from that query is not evidence that its lifetime has ended.
enum WindowServerInfo {
    enum Presence {
        case present([String: Any])
        case absent
        case unavailable
    }

    /// A successful complete catalogue can establish absence. A failed API
    /// cannot; keep this distinction for historical native-window lifetimes.
    static func read(
        _ id: CGWindowID,
        query: (CGWindowListOption, CGWindowID) -> [[String: Any]]? = {
            CGWindowListCopyWindowInfo($0, $1) as? [[String: Any]]
        }
    ) -> Presence {
        func matching(_ rows: [[String: Any]]) -> [String: Any]? {
            rows.first { ($0[kCGWindowNumber as String] as? NSNumber)?.uint32Value == id }
        }
        if let direct = query(.optionIncludingWindow, id), let found = matching(direct) { return .present(found) }
        guard let all = query(.optionAll, kCGNullWindowID) else { return .unavailable }
        return matching(all).map(Presence.present) ?? .absent
    }

    static func window(
        _ id: CGWindowID,
        query: (CGWindowListOption, CGWindowID) -> [[String: Any]] = {
            CGWindowListCopyWindowInfo($0, $1) as? [[String: Any]] ?? []
        }
    ) -> [String: Any]? {
        // Preserve a conflicting direct result for the caller to reject. Do
        // not search by title, geometry or PID for a replacement window.
        if case .present(let row) = read(id, query: { query($0, $1) }) { return row }
        return nil
    }
}
