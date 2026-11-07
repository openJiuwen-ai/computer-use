// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices

/// Query and single-write mechanics for the ORIGINAL row. No ancestor search,
/// focus preparation, mouse fallback, or post-write repair occurs here.
@MainActor enum NativeRowSelection {
    enum Method: String { case replaceRows, selectEmptyRow }
    struct Contract: Equatable {
        let owner: AXUIElement
        let method: Method
        static func == (lhs: Self, rhs: Self) -> Bool { lhs.method == rhs.method && CFEqual(lhs.owner, rhs.owner) }
        var family: String { "macos.axSelection." + method.rawValue + ".v1" }
    }

    static func accepts(role: String?, actions: [String]?, selected: Bool?) -> Bool {
        // Prefer an advertised Press over a selection substitute.
        // Re-clicking an already selected row must retain actual click behavior.
        role == kAXRowRole && actions.map { !$0.contains(kAXPressAction) } == true && selected == false
    }

    static func sample(_ element: AXUIElement, role: String?, actions: [String]?) -> Contract? {
        guard role == kAXRowRole,
            accepts(
                role: role, actions: actions, selected: AXAccess.read(element, kAXSelectedAttribute).value as? Bool),
            let owner = AXAccess.element(element, kAXParentAttribute),
            let ownerRole = AXAccess.read(owner, kAXRoleAttribute).value as? String,
            [kAXTableRole, kAXOutlineRole].contains(ownerRole)
        else { return nil }
        var pid: pid_t = 0, ownerPID: pid_t = 0
        guard AXUIElementGetPid(element, &pid) == .success, AXUIElementGetPid(owner, &ownerPID) == .success,
            pid == ownerPID
        else { return nil }
        // Replacing the container's row set expresses an ordinary single select
        // even when the application allows multiple selection. It is one write.
        if AXAccess.writable(owner, kAXSelectedRowsAttribute).value == true {
            return Contract(owner: owner, method: .replaceRows)
        }
        // Direct AXSelected is equivalent only when no other row is selected.
        // Unknown settable evidence is not a global denial; an explicit false is
        // local to this candidate. A failed write never authorizes a mouse retry.
        if AXAccess.elements(owner, kAXSelectedRowsAttribute, limit: 1)?.isEmpty == true,
            AXAccess.writable(element, kAXSelectedAttribute).value != false
        {
            return Contract(owner: owner, method: .selectEmptyRow)
        }
        return nil
    }

    static func write(_ element: AXUIElement, contract: Contract) -> AXError {
        switch contract.method {
        case .replaceRows:
            return AXMessaging.setAttributeValue(
                contract.owner, kAXSelectedRowsAttribute as CFString, [element] as CFArray)
        case .selectEmptyRow:
            return AXMessaging.setAttributeValue(element, kAXSelectedAttribute as CFString, kCFBooleanTrue)
        }
    }

    static func confirmed(_ element: AXUIElement, contract: Contract) -> Bool {
        guard AXAccess.read(element, kAXSelectedAttribute).value as? Bool == true,
            let rows = AXAccess.elements(contract.owner, kAXSelectedRowsAttribute, limit: 2)
        else { return false }
        return rows.count == 1 && CFEqual(rows[0], element)
    }
}

/// Some tables expose a Press on their opaque cell content that does not
/// select the containing row. Prefer an already ready pointer on that same
/// leaf when the original, unselected row is proved by native relations.
enum NativeOpaqueRowClick {
    static func matches<Object>(
        _ leaf: Object, same: (Object, Object) -> Bool,
        readRole: (Object) throws -> String?, readParent: (Object) throws -> Object?,
        readChildren: (Object) throws -> [Object]?, readRows: (Object) throws -> [Object]?,
        readSelected: (Object) throws -> Bool?, sameProcess: (Object, Object) -> Bool,
        inScope: (Object) throws -> Bool
    ) rethrows -> Bool {
        guard let cell = try readParent(leaf), try readRole(cell) == kAXCellRole,
            try readSelected(cell) == false, let content = try readChildren(cell), content.count == 1,
            same(content[0], leaf), let row = try readParent(cell), try readRole(row) == kAXRowRole,
            try readSelected(row) == false, let cells = try readChildren(row), cells.count == 1,
            same(cells[0], cell), let owner = try readParent(row),
            let ownerRole = try readRole(owner), [kAXTableRole, kAXOutlineRole].contains(ownerRole),
            [cell, row, owner].allSatisfy({ sameProcess(leaf, $0) }), try inScope(owner),
            let rows = try readRows(owner), rows.filter({ same($0, row) }).count == 1
        else { return false }
        return true
    }

    @MainActor static func provesPointerReceiver(
        _ unit: NativeElementPointerUnit, role: String?, enabled: Bool?, focusWritable: Bool,
        actions: [NativeAction.Item]?
    ) throws -> Bool {
        guard role == kAXUnknownRole, enabled == true, !focusWritable,
            let actions, actions.count == 1, actions[0].name == kAXPressAction, actions[0].declarationCount == 1
        else { return false }
        let deadline = ProcessInfo.processInfo.systemUptime + 0.15
        func canQuery() throws -> Bool {
            try RequestDiagnostics.checkCancellation()
            return ProcessInfo.processInfo.systemUptime < deadline
        }
        guard try [kAXValueAttribute, kAXSelectedAttribute, kAXFocusedAttribute].allSatisfy({ key in
            guard try canQuery() else { return false }
            switch AXAccess.read(unit.element, key) {
            case .unsupported, .unavailable(.noValue): return true
            default: return false
            }
        }) else { return false }
        let result = try matches(
            unit.element, same: CFEqual,
            readRole: { try canQuery() ? AXAccess.read($0, kAXRoleAttribute).value as? String : nil },
            readParent: { try canQuery() ? AXAccess.element($0, kAXParentAttribute) : nil },
            readChildren: { try canQuery() ? AXAccess.elements($0, kAXChildrenAttribute, limit: 2) : nil },
            readRows: { try canQuery() ? AXAccess.elements($0, kAXRowsAttribute, limit: 256) : nil },
            readSelected: { try canQuery() ? AXAccess.read($0, kAXSelectedAttribute).value as? Bool : nil },
            sameProcess: { a, b in
                var left: pid_t = 0, right: pid_t = 0
                return AXUIElementGetPid(a, &left) == .success && AXUIElementGetPid(b, &right) == .success
                    && left == right && left == unit.application.processIdentifier
            }, inScope: {
                // Presented rows may omit AXWindow on every node in this
                // chain. Follow their live parents to the retained sheet or
                // window. Transport separately resolves the exact service.
                try unit.hasCurrentParentPath(from: $0, to: unit.logicalWindow ?? unit.window, until: deadline)
            })
        try RequestDiagnostics.checkCancellation()
        return result
    }

    static func actionOffer(_ original: NativeClickContract, preferPointer: Bool) -> NativeClickContract {
        guard preferPointer, original.delivery == .objectAction, original.admission == .ready,
            original.routeFamily == "macos.axAction.AXPress"
        else { return original }
        return .init(
            routeFamily: original.routeFamily, delivery: original.delivery, admission: .insufficientEvidence,
            reason: "Press on this cell content does not establish row selection.", preparationOptions: [])
    }
}

extension NativeElementControl {
    func nativeSelectionContract(_ ref: String) throws -> NativeRowSelection.Contract? {
        guard let target = registry.target(ref) else {
            throw DriverError("element reference expired", code: .staleTarget)
        }
        let facts = try nativeActionFacts(ref)
        guard facts.enabled != false, !facts.applicationMenuContext else { return nil }
        return NativeRowSelection.sample(target.element, role: facts.role, actions: facts.actions?.map(\.name))
    }

    func executeNativeSelection(
        _ ref: String, contract: NativeRowSelection.Contract, onDispatch: @MainActor () throws -> Void,
        onReturned: @MainActor () -> Void
    ) throws -> ActionResult {
        let target = try inputTarget(ref)
        guard try nativeSelectionContract(ref) == contract else {
            throw DriverError("the original row's selection contract changed before dispatch", code: .inputUnavailable)
        }
        try inspection.requireEnabled(target.element)
        try inspection.validateTargetIdentity(target)
        try BackgroundTarget(target.application).validate(input: true)
        try RequestDiagnostics.checkCancellation()
        try onDispatch()
        let status = NativeRowSelection.write(target.element, contract: contract)
        onReturned()
        guard status == .success else {
            throw DriverError(
                "native row selection returned AX error \(status.rawValue); effect unknown, not retried",
                code: .executionUnknown, effect: .possible)
        }
        // A page transition may destroy the row or make its readback unavailable.
        // Preserve the completed write; no selection repair or pointer replay.
        do {
            try RequestDiagnostics.checkCancellation()
            try inspection.validateTargetIdentity(target)
            if NativeRowSelection.confirmed(target.element, contract: contract) {
                return ActionResult(
                    verified: true,
                    detail: "Original row is the sole selected row; application navigation remains unconfirmed.")
            }
        } catch {}
        return ActionResult(
            verified: false,
            detail: "Original row selection dispatched; resulting selection and application navigation are unconfirmed."
        )
    }
}
