// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices

@main enum NativeApplicationKeyboardTests {
    @MainActor final class Menu {
        let nodes = (0..<6).map { AXUIElementCreateApplication(pid_t(8_990_000 + $0)) }
        var foreground = true, enabled = true, duplicate = false, changedItem = false, incomplete = false
        var virtualKey: Int?, modifiers = 0

        func read(_ element: AXUIElement, _ key: String) -> AXRead<CFTypeRef> {
            guard let index = nodes.firstIndex(where: { CFEqual($0, element) }) else { return .invalid }
            switch key {
            case kAXRoleAttribute:
                let roles = [kAXMenuBarRole, kAXMenuBarItemRole, kAXMenuRole, kAXMenuItemRole, kAXMenuItemRole,
                    kAXMenuItemRole]
                return .value(roles[index] as CFString)
            case kAXChildrenAttribute:
                if incomplete && index == 2 { return .unavailable(.cannotComplete) }
                let branches = [[1], [2], [changedItem ? 5 : 3] + (duplicate ? [4] : []), [], [], []]
                return .value(branches[index].map { nodes[$0] } as CFArray)
            case kAXMenuItemCmdCharAttribute: return .value("O" as CFString)
            case kAXEnabledAttribute: return .value(enabled ? kCFBooleanTrue : kCFBooleanFalse)
            case kAXMenuItemCmdModifiersAttribute: return .value(NSNumber(value: modifiers))
            case kAXMenuItemCmdVirtualKeyAttribute: return virtualKey.map { .value(NSNumber(value: $0)) } ?? .unsupported
            default: return .unsupported
            }
        }

        var unit: NativeApplicationKeyboardUnit {
            var unit = NativeApplicationKeyboardUnit(application: .current, menu: nodes[0])
            unit.queries.foreground = { _ in self.foreground }
            unit.queries.read = read
            unit.queries.owner = { _ in getpid() }
            unit.queries.keyCode = { _ in 31 }
            unit.queries.process = { pid in
                NativeProcessIdentity(pid: pid, parent: 1, seconds: 1, microseconds: 0, executable: "/fixture")
            }
            return unit
        }
    }

    @MainActor static func main() throws {
        NSApplication.shared.setActivationPolicy(.prohibited)
        let menu = Menu(), unit = menu.unit
        guard let command = try unit.command("super+o") else {
            preconditionFailure("foreground application menu did not qualify: pid=\(getpid()), app=\(unit.application.processIdentifier)")
        }
        try unit.requireCurrent(command)
        precondition(command.process.pid == getpid() && command.path.count == 4 && command.code == 31)
        let declaration = NativeInputUnit.applicationCommand(command)
            .declaration(id: "command", supported: true, readiness: NativeReadiness())
        precondition(declaration["unit"] as? String == "key_sequence")
        precondition(declaration["channel"] as? String == "keyboard")
        precondition(declaration["globalInput"] as? String == "no")
        precondition(declaration["backgroundPreparation"] as? String == "no")
        for key in ["o", "super+1", "super+shift+o", "alt+o", "Return"] {
            let unsupported = try unit.command(key)
            precondition(unsupported == nil)
        }
        let ambiguous = Menu()
        ambiguous.duplicate = true
        let unsupported = try ambiguous.unit.command("super+o")
        precondition(unsupported == nil)
        for change in ["background", "disabled", "item", "modifier", "virtualKey"] {
            let scene = Menu()
            let original = try scene.unit.command("super+o")!
            switch change {
            case "background": scene.foreground = false
            case "disabled": scene.enabled = false
            case "item": scene.changedItem = true
            case "modifier": scene.modifiers = 8
            default: scene.virtualKey = 35
            }
            do {
                try scene.unit.requireCurrent(original)
                preconditionFailure("changed command was retained: \(change)")
            } catch let failure as DriverError { precondition(failure.code == .staleTarget) }
        }
        menu.incomplete = true
        do {
            _ = try unit.command("super+o")
            preconditionFailure("incomplete menu was accepted")
        } catch let failure as DriverError { precondition(failure.code == .observationIncomplete) }
        print("PASS windowless application command evidence and final shortcut revalidation")
    }
}
