// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices

/// Electron reports the current AppKit event's source to its menu callback.
/// An addressed Press on an accelerator leaf can run a keyboard binding
/// instead of the menu command selected by a mouse click.
enum NativeMenuCommandPolicy {
    static let pointerFamily = "macos.menuCommand.windowDirected.v1"

    static func requiresPointer(
        electronFramework: Bool, action: String, role: String?, hasSubmenu: Bool?, commandCharacter: String?
    ) -> Bool {
        electronFramework && action == kAXPressAction && role == kAXMenuItemRole && hasSubmenu == false
            && commandCharacter?.isEmpty == false
    }

    @MainActor static func requiresPointer(_ unit: NativeMenuUnit, action: String, role: String?) -> Bool {
        guard action == kAXPressAction, role == kAXMenuItemRole,
            let framework = unit.application.bundleURL?
                .appendingPathComponent("Contents/Frameworks/Electron Framework.framework"),
            Bundle(url: framework)?.bundleIdentifier == "com.github.Electron.framework"
        else { return false }
        let children = AXAccess.read(unit.element, kAXChildrenAttribute)
        let hasSubmenu: Bool?
        switch children {
        case .unsupported, .unavailable(.noValue): hasSubmenu = false
        case .value(let value):
            let roles = (value as? [AXUIElement]).flatMap { elements -> [String?]? in
                guard elements.count <= 64 else { return nil }
                return elements.map { AXAccess.read($0, kAXRoleAttribute).value as? String }
            }
            hasSubmenu = roles.flatMap { roles in
                roles.contains(kAXMenuRole) ? true : roles.contains(nil) ? nil : false
            }
        default: hasSubmenu = nil
        }
        return requiresPointer(
            electronFramework: true, action: action, role: role, hasSubmenu: hasSubmenu,
            commandCharacter: AXAccess.read(unit.element, kAXMenuItemCmdCharAttribute).value as? String)
    }

    static func offer(ready: Bool, available: Bool, canPrepare: Bool) -> NativeClickContract {
        let supported = available && (ready || canPrepare)
        return NativeClickContract(
            routeFamily: pointerFamily, delivery: .windowDirected,
            admission: !supported ? .unavailable : ready ? .ready : .preparationRequired,
            reason: supported ? nil : "The menu command requires a frontmost window-directed mouse receiver.",
            preparationOptions: supported && !ready ? ["foreground"] : [],
            backgroundUnavailable: true)
    }
}
