// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices

/// DISPATCH: enter exactly the selected native delivery, preserving every input barrier and paired release.
@MainActor enum DispatchNativeClickPass {
    static func run(
        _ plan: NativeClickPlan, boundary: NativeUnitBoundary, receipt: NativeExecutionReceipt,
        evidence: NativeClickEvidence, objects: any NativeControlObjectAccess,
        resolveWindow: () throws -> NativeFixedInputWindow? = { nil },
        preparedPoint: NativePreparedPointerPoint? = nil,
        elementGeometry: NativeElementPointerUnit.Geometry? = nil,
        resolveCapture: (() throws -> NativeCapture.Input)? = nil
    ) async throws {
        let metric = RequestDiagnostics.metrics?.start(.clickDispatch)
        defer { metric?.end() }
        func guardOperation() throws {
            try plan.operation.check(write: true)
            try RequestDiagnostics.checkCancellation()
        }
        func actionBoundary() throws {
            try boundary.checkConditions()
            var retained = plan.actionDocument
            if retained == nil, let scope = plan.documentScope, case .action(let node, let name, _) = plan.target,
                name == kAXPressAction
            {
                retained = try plan.operation.actionDocuments.context(scope) {
                    try objects.nativeActionFacts(node).resolvingDocument(resolveWindow).document
                }
            }
            try NativeActionDocumentPolicy.checkDispatch(
                retained?.unit ?? plan.document,
                required: plan.documentPreparation.windowSelectionRequired || retained?.selectionRequired == true,
                retained: retained)
            try boundary.beforeDispatch()
            evidence.markCall("nativeAction")
        }
        func pointerBoundary() throws {
            try boundary.markDispatchInFlight()
            evidence.markCall("posted")
        }
        func posted() {
            receipt.recordAccepted()
            evidence.markReturned()
        }
        var selectionResult: NativeElementControl.ActionResult?
        switch plan.target {
        case .selection(let node, let contract):
            selectionResult = try objects.executeNativeSelection(
                node, contract: contract,
                onDispatch: {
                    try boundary.beforeDispatch()
                    evidence.markCall("nativeSelection")
                }, onReturned: evidence.markReturned)
        case .coordinateMenuPress(let press):
            guard let resolveCapture else {
                throw DriverError("coordinate menu Press lacks its original image", code: .invalidRequest)
            }
            try guardOperation()
            try press.requireCurrent(resolveCapture())
            try actionBoundary()
            // Reuse the same terminal AX dispatch as Element Press. A proven
            // already-open menu does not inherit menu-bar/document preparation.
            let status = AXMessaging.performAction(press.element, kAXPressAction as CFString)
            evidence.markReturned()
            guard status == .success else {
                throw DriverError("coordinate menu Press returned AX error \(status.rawValue); effect unknown, not retried",
                                  code: .executionUnknown, effect: .possible)
            }
            try await CompleteNativeCoordinateMenuPressPass.run(
                windowID: press.windowID, pid: press.application.processIdentifier,
                receipt: receipt, evidence: evidence, deadline: plan.operation.deadline, check: guardOperation)
        case .action(let node, let name, let description):
            try objects.executeNativeAction(
                node, action: name, description: description, onDispatch: actionBoundary,
                onReturned: evidence.markReturned)
        case .menu(let unit, let node, let name, let description):
            let facts = try objects.nativeActionFacts(node)
            guard NativeAction.matches(facts.actions, name: name, description: description) else {
                throw DriverError("the selected menu action declaration changed", code: .inputUnavailable)
            }
            try await unit.perform(
                action: name, opensMenu: false, check: boundary.checkConditions, onDispatch: actionBoundary,
                onReturned: evidence.markReturned)
        case .menuPointer(let unit):
            try await unit.performPointer(
                plan.pointer!, check: boundary.checkConditions, guardOperation: guardOperation,
                onDispatch: pointerBoundary, onPosted: posted, onDelivered: { receipt.dispatch = "dispatched" },
                onPreparation: receipt.recordPreparation,
                onCleanupUnknown: receipt.recordCleanupUnknown, onNativePreparation: evidence.record)
        case .captured(let unit):
            try await unit.deliver(
                plan.pointer!, check: boundary.checkConditions, guardOperation: guardOperation,
                onDispatch: pointerBoundary, onPosted: posted,
                onCleanupUnknown: receipt.recordCleanupUnknown, delivery: plan.offer.delivery,
                onPreparation: receipt.recordPreparation, onNativePreparation: evidence.record,
                preparedPoint: preparedPoint)
        case .element(let unit):
            guard let elementGeometry else {
                throw DriverError("the selected element click has no prepared point", code: .invalidRequest)
            }
            // Verify this same point before each event. Searching again here
            // could switch the ready candidate after preparation.
            try await unit.deliver(
                plan.pointer!, check: boundary.checkConditions, guardOperation: guardOperation,
                onDispatch: pointerBoundary, onPosted: posted,
                onCleanupUnknown: receipt.recordCleanupUnknown, delivery: plan.offer.delivery,
                onPreparation: receipt.recordPreparation, onNativePreparation: evidence.record,
                preparedGeometry: elementGeometry)
        case .popupPointer(let unit):
            try await unit.deliver(
                plan.pointer!, check: boundary.checkConditions, onDispatch: pointerBoundary, onPosted: posted,
                onCleanupUnknown: receipt.recordCleanupUnknown)
        }
        receipt.dispatch = "dispatched"
        if let result = selectionResult {
            receipt.detail = result.detail
            receipt.confirmation = result.verified ? "confirmed" : "unavailable"
        } else {
            receipt.detail = "Click dispatched through the selected native delivery."
        }
        evidence.complete()
    }
}
