// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices

struct NativeUnitBoundary {
    let checkConditions: @MainActor () throws -> Void
    /// Parent route supplies only this dispatch's explicit scope query.
    var keyboardDispatchCheck:
        @MainActor (@escaping @MainActor () throws -> [String]) -> (@MainActor () throws -> Void) = { _ in
            { throw DriverError("keyboard dispatch boundary adapter unavailable", code: .invalidRequest) }
        }
    let checkSelection: @MainActor () throws -> Void
    let markDispatchInFlight: @MainActor () throws -> Void
    /// Scoped keyboard validates selection before its final receiver read.
    let checkKeyboardSelection: @MainActor () throws -> Void
    let markPreparedKeyboardDispatchInFlight: @MainActor () throws -> Void
    let beforeDispatch: @MainActor () throws -> Void
    /// Same-actor guard/mark callbacks are consumed immediately by the chosen unit.
    @MainActor static func make(
        _ command: NativeUnitCommand, op: NativeOperation, resolved: ResolvedNativeTarget,
        receipt: NativeExecutionReceipt, access: NativeControlAccess
    ) -> NativeUnitBoundary {
        let bound = command.target, contract = resolved.contract, binding = resolved.binding
        @MainActor func checkBoundaryScope(_ sample: () throws -> [String]) throws {
            try op.check(write: true)
            let blocked = try sample()
            guard blocked.isEmpty else {
                throw DriverError(
                    "target became blocked by modal \(blocked.joined(separator: ", "))", code: .blockedByModal)
            }
            try access.scopes.checkContainment(bound, within: contract.within, op: op)
            try access.scopes.checkActuator(bound, actuator: contract.actuator, op: op)
            if contract.actuator?.relation == "scrollPointer" {
                // A preceding mouseMoved/entered event may change a
                // passive hit into another scrolling/input region.
                // Check the selected facts before EVERY wheel event,
                // not merely at the sequence's first dispatch barrier.
                try access.contracts.selectionBasis(op.token).check(
                    contract.basis, scope: string(binding.wire["surface"], label: "surface"))
            }
            try op.check(write: true)
            try RequestDiagnostics.checkCancellation()
        }
        @MainActor func checkBoundary() throws {
            try checkBoundaryScope {
                if let proof = contract.click?.popupPointer {
                    guard case .pointer(.click) = contract.unit, contract.actuator == nil, contract.receiver == nil,
                        binding.node != nil, !(binding.wire["capture"] is String), !command.foreground,
                        contract.click?.delivery == .systemPointer,
                        NativePopupClickPolicy.permitsIntent(op.request)
                    else { throw DriverError("popup point qualification used by another route", code: .invalidRequest) }
                    return try access.scopes.popupPointerBoundary(command.value["target"], qualification: proof)
                }
                let attachedMenuAction: Bool
                if case .action = contract.unit { attachedMenuAction = true } else { attachedMenuAction = false }
                return try access.scopes.inputBinding(
                    command.value["target"], input: true, attachedMenuAction: attachedMenuAction
                ).blocked
            }
        }
        @MainActor func checkSelection() throws {
            try access.contracts.selectionBasis(op.token).check(
                contract.basis,
                scope: access.scopes.isDesktop(binding.wire)
                    ? access.desktop().scope : string(binding.wire["surface"], label: "surface"))
        }
        var keyboardSelectionChecked = false
        @MainActor func checkKeyboardSelection() throws {
            if receipt.dispatch == "notDispatched" {
                try checkSelection()
                keyboardSelectionChecked = true
            }
        }
        @MainActor func markPreparedKeyboardDispatch() throws {
            guard receipt.dispatch != "notDispatched" || keyboardSelectionChecked else {
                throw DriverError("scoped keyboard selection admission was not prepared", code: .invalidRequest)
            }
            try markDispatch(checkSelectionFirst: false)
            keyboardSelectionChecked = false
        }
        @MainActor func markDispatch(checkSelectionFirst: Bool = true) throws {
            try op.check(write: true)
            if checkSelectionFirst && receipt.dispatch == "notDispatched" { try checkSelection() }
            try RequestDiagnostics.checkCancellation()
            receipt.dispatch = "unknown"
            op.recordInputDispatch()
            access.recordEffect()
        }
        @MainActor func beforeDispatch() throws {
            try checkBoundary()
            try markDispatch()
        }
        return NativeUnitBoundary(
            checkConditions: checkBoundary, keyboardDispatchCheck: { sample in { try checkBoundaryScope(sample) } },
            checkSelection: checkSelection, markDispatchInFlight: { try markDispatch() },
            checkKeyboardSelection: checkKeyboardSelection,
            markPreparedKeyboardDispatchInFlight: markPreparedKeyboardDispatch, beforeDispatch: beforeDispatch)

    }
}
