// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices

/// One admitted click attempt. Candidate selection and any later attempt stay
/// in the public Gate; this native boundary never changes the selected delivery.
@MainActor enum NativeClickPipeline {
    static func run(
        _ command: NativeUnitCommand, resolved: ResolvedNativeTarget, operation: NativeOperation, step: Int64,
        boundary: NativeUnitBoundary, receipt: NativeExecutionReceipt, access: NativeControlAccess
    ) async {
        let evidence = receipt.click!
        if let offer = resolved.contract.click { evidence.select(offer.routeFamily) }
        do {
            let plan = try ResolveNativeClickPlanPass.run(
                command, resolved: resolved, op: operation, step: step, access: access)
            func execute() async throws {
                var preparedPoint: NativePreparedPointerPoint?
                let elementGeometry = try await NativeInputActivity.$stage.withValue(.preparation) {
                    let geometry = try await NativeClickPreparationPipeline.run(
                        plan, boundary: boundary, receipt: receipt, evidence: evidence, objects: access.objects)
                    if command.foreground, plan.offer.qtComboChoice == nil, case .captured(let unit) = plan.target {
                        preparedPoint = try await unit.prepareBackgroundPoint(
                            plan.pointer!, check: boundary.checkConditions,
                            onPreparation: receipt.recordPreparation, onCleanupUnknown: receipt.recordCleanupUnknown,
                            onNativePreparation: evidence.record)
                    }
                    return geometry
                }
                NativeForegroundRestorationScope.current?.willDispatchInput()
                try await DispatchNativeClickPass.run(
                    plan, boundary: boundary, receipt: receipt, evidence: evidence, objects: access.objects,
                    resolveWindow: { try access.scopes.fixedInputWindow(command.target) }, preparedPoint: preparedPoint,
                    elementGeometry: elementGeometry)
            }
            let application: NSRunningApplication?
            switch plan.target {
            case .element(let unit): application = unit.application
            case .captured(let unit): application = unit.application
            case .menu(let unit, _, _, _): application = unit.application
            case .menuPointer(let unit): application = unit.application
            default: application = nil
            }
            func knownTransientResult() throws -> Bool {
                if plan.qtPopupOpener != nil { return true }
                let element: AXUIElement?
                switch plan.target {
                case .action(_, let name, _): return name == kAXShowMenuAction
                case .menu(_, let node, let name, _):
                    if name == kAXShowMenuAction { return true }
                    let facts = try? access.objects.nativeActionFacts(node)
                    return facts?.role == kAXMenuBarItemRole
                case .element(let unit): element = unit.member ?? unit.element
                default: element = nil
                }
                guard let element else { return false }
                let role = AXAccess.read(element, kAXRoleAttribute).value as? String
                if [kAXMenuRole, kAXMenuButtonRole, kAXPopUpButtonRole, kAXComboBoxRole, "AXPopover"].contains(role ?? "") {
                    return true
                }
                return AXAccess.read(element, kAXExpandedAttribute).value as? Bool == true
            }
            if command.foreground, let application {
                try await NativeForegroundRestorationScope.perform(
                    target: BackgroundTarget(application), onPreparation: receipt.recordPreparation,
                    onCleanupUnknown: receipt.recordCleanupUnknown, afterInput: true,
                    inputState: {
                        receipt.dispatch == "notDispatched" ? .notDispatched
                            : receipt.dispatch == "dispatched" ? .completed : .uncertain
                    }, knownTransient: knownTransientResult, body: execute)
            } else {
                try await execute()
            }
        } catch {
            var failure = error
            if let observation = error as? NativeTogglePress.Unconfirmed {
                do {
                    if let result = try await ConfirmNativeToggleEffectPass.run(observation, boundary: boundary) {
                        receipt.dispatch = "dispatched"
                        receipt.detail = result.detail
                        receipt.confirmation = "confirmed"
                        evidence.complete()
                        return
                    }
                } catch { failure = error }
            }
            BuildNativeClickFailurePass.run(
                failure, command: command, operation: operation, step: step, offer: resolved.contract.click,
                within: resolved.contract.within, receipt: receipt, evidence: evidence)
        }
    }

    static func failResolution(
        _ error: Error, command: NativeUnitCommand, operation: NativeOperation, step: Int64,
        receipt: NativeExecutionReceipt
    ) {
        BuildNativeClickFailurePass.run(
            error, command: command, operation: operation, step: step, offer: nil, within: [], receipt: receipt,
            evidence: receipt.click!)
    }
}
