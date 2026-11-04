// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import ApplicationServices

/// Evidence only for optional restitution after a completed click. Absence of
/// a new physical window cannot prove that an inline menu is safe to dismiss.
enum NativeForegroundResult {
    struct Window {
        let id: CGWindowID
        let role: String?
        let subrole: String?
        let modal: Bool?
    }
    enum Decision: String { case persistentResult, noProvenPersistentResult, transientResult, unknownResult }

    static func persistentWindow(role: String?, subrole: String?, modal: Bool?) -> Bool {
        guard role == kAXWindowRole else { return false }
        return subrole == kAXStandardWindowSubrole
            || modal == true && [kAXDialogSubrole, kAXSystemDialogSubrole].contains(subrole ?? "")
    }

    static func decide(
        before: Set<CGWindowID>?, after: Set<CGWindowID>?, windows: [Window], knownTransient: Bool
    ) -> Decision {
        if knownTransient { return .transientResult }
        guard let before, let after else { return .unknownResult }
        let new = after.subtracting(before)
        guard !new.isEmpty else { return .noProvenPersistentResult }
        for id in new {
            let matches = windows.filter { $0.id == id }
            guard matches.count == 1, let value = matches.first else { return .unknownResult }
            if [kAXMenuRole, "AXPopover"].contains(value.role ?? "") { return .transientResult }
            guard persistentWindow(role: value.role, subrole: value.subrole, modal: value.modal) else { return .unknownResult }
        }
        return .persistentResult
    }
}
