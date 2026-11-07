// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices

/// A command advertised by the retained menu of an already foreground App.
/// The menu supplies recipient evidence; delivery is still a keyboard chord.
@MainActor struct NativeApplicationKeyboardUnit {
    let application: NSRunningApplication
    let menu: AXUIElement
    var queries = Queries()

    @MainActor struct Queries {
        var foreground: (pid_t) -> Bool = { pid in
            NSWorkspace.shared.frontmostApplication?.processIdentifier == pid
                && AXAccess.read(AXUIElementCreateApplication(pid), kAXFrontmostAttribute).value as? Bool == true
        }
        var process: (pid_t) -> NativeProcessIdentity? = NativeProcessIdentity.read
        var keyCode: @MainActor (String) throws -> CGKeyCode? = Input.applicationCommandCode
        var read: (AXUIElement, String) -> AXRead<CFTypeRef> = { AXAccess.read($0, $1) }
        var owner: (AXUIElement) -> pid_t? = {
            var pid: pid_t = 0
            return AXUIElementGetPid($0, &pid) == .success ? pid : nil
        }
    }

    struct Command: Equatable {
        let key: String
        let code: CGKeyCode
        let process: NativeProcessIdentity
        let path: [AXUIElement]

        static func == (lhs: Self, rhs: Self) -> Bool {
            lhs.key == rhs.key && lhs.code == rhs.code && lhs.process == rhs.process
                && lhs.path.count == rhs.path.count && zip(lhs.path, rhs.path).allSatisfy { CFEqual($0.0, $0.1) }
        }
    }

    func isForeground() -> Bool { queries.foreground(application.processIdentifier) }

    private func children(_ element: AXUIElement) throws -> [AXUIElement] {
        switch queries.read(element, kAXChildrenAttribute) {
        case .value(let value):
            if let children = value as? [AXUIElement], children.count <= 512 { return children }
        case .unsupported, .unavailable(.noValue): return []
        default: break
        }
        throw DriverError("application command menu could not be read within its native budget", code: .observationIncomplete)
    }

    private func advertises(_ element: AXUIElement, letter: String, code: CGKeyCode) -> Bool {
        guard queries.read(element, kAXRoleAttribute).value as? String == kAXMenuItemRole,
            (queries.read(element, kAXMenuItemCmdCharAttribute).value as? String)?.lowercased() == letter,
            queries.read(element, kAXEnabledAttribute).value as? Bool == true,
            // Accessibility uses a zero mask to describe Command by itself.
            (queries.read(element, kAXMenuItemCmdModifiersAttribute).value as? NSNumber)?.uint32Value == 0
        else { return false }
        switch queries.read(element, kAXMenuItemCmdVirtualKeyAttribute) {
        case .value(let value): return (value as? NSNumber)?.uint16Value == code
        case .unsupported, .unavailable(.noValue): return true
        default: return false
        }
    }

    func command(_ key: String) throws -> Command? {
        let parts = key.split(separator: "+", omittingEmptySubsequences: false)
        let pid = application.processIdentifier
        guard parts.count == 2, parts[0] == "super", parts[1].utf8.count == 1,
            parts[1].utf8.first.map({ (97...122).contains($0) }) == true,
            isForeground(), let process = queries.process(pid), process.pid == pid,
            queries.read(menu, kAXRoleAttribute).value as? String == kAXMenuBarRole,
            let code = try queries.keyCode(key)
        else { return nil }
        let letter = String(parts[1])
        var pending = [[menu]], cursor = 0, found: [AXUIElement]?
        var seen = [AXUIElement]()
        while cursor < pending.count {
            try RequestDiagnostics.checkCancellation()
            let path = pending[cursor], current = path.last!
            cursor += 1
            guard path.count <= 12, queries.owner(current) == pid,
                !seen.contains(where: { CFEqual($0, current) })
            else { return nil }
            seen.append(current)
            if advertises(current, letter: letter, code: code) {
                guard found == nil else { return nil }
                found = path
            }
            let descendants = try children(current)
            guard pending.count + descendants.count <= 512 else { return nil }
            pending += descendants.map { path + [$0] }
        }
        guard let path = found, queries.process(pid) == process, isForeground() else { return nil }
        return Command(key: key, code: code, process: process, path: path)
    }

    func requireCurrent(_ original: Command) throws {
        let pid = application.processIdentifier
        // Discovery establishes uniqueness once. The per-event boundary
        // revalidates the original menu path and process without a tree scan.
        guard original.process.pid == pid, queries.process(pid) == original.process, isForeground(),
            try queries.keyCode(original.key) == original.code,
            let item = original.path.last, original.path.allSatisfy({ queries.owner($0) == pid }),
            try ObservedPath.matches(original.path, root: menu, same: { CFEqual($0, $1) }, children: children),
            advertises(item, letter: String(original.key.suffix(1)), code: original.code),
            queries.process(pid) == original.process, isForeground()
        else {
            throw DriverError("the windowless application or its command shortcut changed", code: .staleTarget)
        }
    }

    static func route(_ context: NativeRouteContext, command: Command) throws -> SelectedNativeRoute {
        let selected = context.resolved.contract, target = context.command.target
        let request = try NativeKeyboardIntent(
            unit: context.command.kind, kind: context.operation.intent, parameters: context.operation.request.parameters,
            foreground: context.command.foreground)
        guard request.kind == .key, request.key == command.key,
            context.command.kind == "key_sequence", !context.command.foreground,
            selected.within.isEmpty, selected.actuator == nil, selected.receiver == nil,
            let unit = try context.access.units.applicationKeyboardUnit(target)
        else { throw DriverError("application command does not match the selected menu scope", code: .invalidRequest) }
        return .dispatch {
            let pid = command.process.pid
            let recipient = NativeKeyboardRecipient(
                pid: pid, window: nil, remoteElement: nil, presenter: pid,
                process: nil, axWindow: nil, remoteRoute: nil, endpoint: nil, endpointProcess: nil)
            try await Input.preparedKey(
                command.key, delivery: .process(recipient), validate: {
                    try context.boundary.checkConditions()
                    try unit.requireCurrent(command)
                }, onDispatch: context.boundary.markDispatchInFlight, onPosted: context.receipt.recordAccepted,
                onCleanupUnknown: context.receipt.recordCleanupUnknown)
            context.receipt.detail = "Command keys dispatched to the original application; effect unconfirmed."
            return false
        }
    }
}
