// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices

/// An accessibility provider can report only a one-point strip of the control
/// it clips. An addressed reveal is preparation; the ancestor is never a click
/// receiver and fresh hit testing still owns the requested click.
struct NativeClickClippedLeaf: Equatable {
    private static let action = "AXScrollToVisible"
    private static let family = "macos.windowDirected.v1"
    let region: AXUIElement
    let passiveAncestor: AXUIElement
    let viewport: CGRect
    let edge: NativeElementViewport.VerticalClip.Edge
    let window: CGWindowID
    let pid: pid_t

    static func == (lhs: Self, rhs: Self) -> Bool {
        lhs.window == rhs.window && lhs.pid == rhs.pid && lhs.edge == rhs.edge && lhs.viewport == rhs.viewport
            && CFEqual(lhs.region, rhs.region) && CFEqual(lhs.passiveAncestor, rhs.passiveAncestor)
    }

    var offer: NativeClickContract {
        .init(
            routeFamily: Self.family, delivery: .windowDirected, admission: .preparationRequired,
            reason: "The original element is clipped at a scroll viewport edge.", preparationOptions: ["background"],
            clippedLeaf: self, requiresExactElementHit: true)
    }

    static func continuationOffer(ready: Bool) -> NativeClickContract {
        .init(
            routeFamily: family, delivery: .windowDirected, admission: ready ? .ready : .insufficientEvidence,
            reason: ready ? nil : "The revealed original element's directed click is not ready.",
            preparationOptions: [], requiresExactElementHit: true)
    }

    static func permitsContinuation(_ offer: NativeClickContract) -> Bool { offer == continuationOffer(ready: true) }

    @MainActor static func sample(
        _ unit: NativeElementPointerUnit, hit: NativeElementPointerUnit.Observation, retaining proof: Self? = nil
    ) throws -> Self? {
        guard !hit.dispatchGeometryReady, hit.preparation == .unavailable, hit.geometry.visible.height <= 1,
            unit.member == nil, unit.qtComboChoice == nil, let currentHit = hit.hit, !CFEqual(currentHit, unit.element),
            unit.logicalWindow.map({ CFEqual($0, unit.window) }) != false
        else { return nil }
        let ancestor = proof?.passiveAncestor ?? currentHit
        let geometry = try unit.viewport()
        let mapping = geometry.mapping
        guard let clip = geometry.verticalClip, geometry.clipped == hit.geometry.visible,
            mapping.id == hit.geometry.mapping.id, mapping.pid == hit.geometry.mapping.pid,
            AXAccess.read(unit.element, kAXEnabledAttribute).value as? Bool == true,
            AXAccess.read(unit.element, "AXHidden").value as? Bool != true,
            let role = AXAccess.read(unit.element, kAXRoleAttribute).value as? String,
            [kAXButtonRole, kAXCheckBoxRole, kAXRadioButtonRole, "AXLink"].contains(role),
            unit.inOriginalWindow(unit.element, pid: mapping.pid), unit.inOriginalWindow(clip.region, pid: mapping.pid),
            unit.inOriginalWindow(ancestor, pid: mapping.pid),
            CFEqual(currentHit, ancestor) || unit.inOriginalWindow(currentHit, pid: mapping.pid)
        else { return nil }
        var childCount = 0
        guard
            AXUIElementGetAttributeValueCount(unit.element, kAXChildrenAttribute as CFString, &childCount) == .success,
            childCount == 0, let actions = actionNames(unit.element), actions.contains(Self.action),
            actions.contains(kAXPressAction),
            let ancestorRole = AXAccess.read(ancestor, kAXRoleAttribute).value as? String,
            [kAXGroupRole, kAXScrollAreaRole, "AXWebArea"].contains(ancestorRole),
            let ancestorActions = actionNames(ancestor),
            ancestorActions.allSatisfy({ [Self.action, kAXShowMenuAction].contains($0) })
        else { return nil }
        let deadline = ProcessInfo.processInfo.systemUptime + 0.15
        guard try unit.hasCurrentParentPath(from: unit.element, to: ancestor, until: deadline),
            try
                (CFEqual(ancestor, clip.region)
                || unit.hasCurrentParentPath(from: ancestor, to: clip.region, until: deadline)),
            try confirmsHitContext(
                currentHit, ancestor: ancestor, same: CFEqual,
                reaches: { try unit.hasCurrentParentPath(from: $0, to: $1, until: deadline) }),
            BackgroundEvents.inputWindowHit(at: hit.geometry.point, retained: mapping, nativeWindow: unit.window)
                .windowID == mapping.id
        else { return nil }
        do { try BackgroundEvents.requirePointerRoute(window: mapping) } catch is NativePreparationUnavailable {
            return nil
        }
        try RequestDiagnostics.checkCancellation()
        return Self(
            region: clip.region, passiveAncestor: ancestor, viewport: clip.viewport, edge: clip.edge,
            window: mapping.id, pid: mapping.pid)
    }

    /// The fresh point sample can return another node under the already proved
    /// passive ancestor. This proves context for the addressed reveal only.
    static func confirmsHitContext<Node>(
        _ hit: Node, ancestor: Node, same: (Node, Node) -> Bool, reaches: (Node, Node) throws -> Bool
    ) rethrows -> Bool { try same(hit, ancestor) || reaches(hit, ancestor) }

    @MainActor func reveal(
        _ unit: NativeElementPointerUnit, operation: NativeOperation, boundary: NativeUnitBoundary,
        receipt: NativeExecutionReceipt, evidence: NativeClickEvidence
    ) async throws {
        try boundary.checkConditions()
        let hit = try unit.observedGeometry()
        // The target may already be visible by the time the offer executes.
        if hit.dispatchGeometryReady { return }
        guard !operation.didRevealClippedClickLeaf, let current = try Self.sample(unit, hit: hit, retaining: self),
            current == self
        else {
            throw DriverError("the selected element's clipping evidence changed before reveal", code: .inputUnavailable)
        }
        try boundary.checkConditions()
        try BackgroundTarget(unit.application).validate(input: true)
        if WindowGeometry.inputDiagnosticsEnabled {
            diagnose([
                "kind": "clipped_click_revalidation", "window": window,
                "samePassiveHit": hit.hit.map { CFEqual($0, passiveAncestor) } == true,
                "point": [hit.geometry.point.x, hit.geometry.point.y]
            ])
        }
        try performOnce(operation: operation, receipt: receipt, evidence: evidence) { onCall in
            AXMessaging.performAction(unit.element, Self.action as CFString, onCall: onCall)
        }
        let waitUntil = min(operation.deadline, ProcessInfo.processInfo.systemUptime + 0.4)
        repeat {
            try boundary.checkConditions()
            let sample = try unit.viewport()
            guard sample.mapping.id == window, sample.mapping.pid == pid else {
                throw DriverError("the original element's reveal window changed", code: .staleTarget)
            }
            if let live = WindowGeometry.hint(region)?.frame,
                NativeElementViewport.VerticalClip.isExposed(
                    frame: sample.frame, clipped: sample.clipped, viewport: live)
            {
                let deadline = min(operation.deadline, ProcessInfo.processInfo.systemUptime + 0.15)
                guard unit.inOriginalWindow(region, pid: pid),
                    try unit.hasCurrentParentPath(from: unit.element, to: region, until: deadline)
                else { throw DriverError("the original element's reveal viewport changed", code: .staleTarget) }
                evidence.confirmViewportProgress(window: window)
                return
            }
            try await Task.sleep(for: .milliseconds(20))
        } while ProcessInfo.processInfo.systemUptime < waitUntil
        throw DriverError("the original element was not exposed by its AX action", code: .inputUnavailable)
    }

    @MainActor func performOnce(
        operation: NativeOperation, receipt: NativeExecutionReceipt, evidence: NativeClickEvidence,
        _ perform: (() -> Void) -> AXError
    ) throws {
        guard !operation.didRevealClippedClickLeaf else {
            throw DriverError("the original element's reveal was already attempted", code: .inputUnavailable)
        }
        let detail = "reveal the original clipped element through AXScrollToVisible"
        let status = perform {
            operation.recordClippedClickLeafReveal()
            receipt.recordPreparation(detail)
            evidence.record(.init(kind: .viewport, phase: .willSend, detail: detail, window: window))
        }
        if operation.didRevealClippedClickLeaf {
            evidence.record(.init(kind: .viewport, phase: .returned, detail: detail, window: window))
        }
        guard status == .success else {
            throw DriverError(
                "the original element's reveal returned AX error \(status.rawValue)", code: .inputUnavailable)
        }
    }

    private static func actionNames(_ object: AXUIElement) -> [String]? {
        var names: CFArray?
        return AXUIElementCopyActionNames(object, &names) == .success ? names as? [String] : nil
    }
}
