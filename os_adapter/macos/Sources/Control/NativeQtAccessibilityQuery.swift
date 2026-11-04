// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices

/// QUERY-only provider classification on the addressed native object. The Qt
/// identifier signature is independent of this live original-window path;
/// neither one substitutes for dispatch-time identity or pointer qualification.
@MainActor enum NativeQtAccessibilityQuery {
    static func isWidget(
        _ element: AXUIElement, identifier: String?, originalWindow: AXUIElement?, presenterPID: pid_t
    ) throws -> Bool {
        guard NativeQtAccessibilityIdentifier.isWidgetPath(identifier), let originalWindow else { return false }
        func sameProcess(_ node: AXUIElement) -> Bool {
            var pid: pid_t = 0
            return AXUIElementGetPid(node, &pid) == .success && pid == presenterPID
        }
        guard sameProcess(element), sameProcess(originalWindow) else { return false }
        return try NativeElementHitSelection.reachesWindow(
            element, window: originalWindow, same: { CFEqual($0, $1) },
            parent: { node in
                guard let parent = AXAccess.element(node, kAXParentAttribute), sameProcess(parent) else { return nil }
                return parent
            },
            isSurface: { node in
                guard let role = AXAccess.read(node, kAXRoleAttribute).value as? String else { return true }
                return [kAXWindowRole, kAXSheetRole, "AXPopover"].contains(role)
            },
            canContinue: {
                try RequestDiagnostics.checkCancellation()
                return true
            })
    }
}
