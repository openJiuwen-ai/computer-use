import AppKit
import ApplicationServices

/// DISPATCH: enter exactly the selected native delivery, preserving every input barrier and paired release.
@MainActor enum DispatchNativeClickPass {
    static func run(
        _ plan: NativeClickPlan, boundary: NativeUnitBoundary, receipt: NativeExecutionReceipt,
        evidence: NativeClickEvidence, objects: any NativeControlObjectAccess
    ) async throws {
        let metric = RequestDiagnostics.metrics?.start(.clickDispatch)
        defer { metric?.end() }
        func actionBoundary() throws {
            try boundary.checkConditions()
            if let document = plan.document,
                try
                    (!NativeDocumentContext.permitsDispatch(
                        document.focus(), prepared: plan.documentPreparationRequired)
                    || (plan.documentPreparationRequired && !BackgroundTarget(document.application).isForeground))
            {
                throw NativePreparationUnavailable(
                    description: "document context changed before AXPress; no requested action was sent")
            }
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
        case .captured(let unit):
            try await unit.deliver(
                plan.pointer!, check: boundary.checkConditions, onDispatch: pointerBoundary, onPosted: posted,
                onCleanupUnknown: receipt.recordCleanupUnknown, delivery: plan.offer.delivery)
        case .element(let unit):
            // This samples the original element after preparation. Only this
            // ensuing gesture freezes geometry; no new screenshot RPC is needed.
            try await unit.deliver(
                plan.pointer!, check: boundary.checkConditions, onDispatch: pointerBoundary, onPosted: posted,
                onCleanupUnknown: receipt.recordCleanupUnknown, delivery: plan.offer.delivery)
        case .desktop(let unit):
            try await unit.deliver(
                plan.pointer!, check: boundary.checkConditions, onDispatch: pointerBoundary, onPosted: posted,
                onCleanupUnknown: receipt.recordCleanupUnknown)
        }
        receipt.dispatch = "dispatched"
        if let result = selectionResult {
            receipt.detail = result.detail
            receipt.confirmation = result.verified ? "confirmed" : "unavailable"
        } else {
            receipt.detail = "Click dispatched through the selected native delivery; application effect unconfirmed."
        }
        evidence.complete()
    }
}
