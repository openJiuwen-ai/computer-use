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
