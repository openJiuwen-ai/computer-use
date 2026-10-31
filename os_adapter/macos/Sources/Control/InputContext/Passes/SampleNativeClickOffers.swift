// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices

/// QUERY/RESOURCE: publish fixed-delivery click offers without preparing any
/// route. All facts remain bound to the existing operation and target.
@MainActor enum SampleNativeClickOffersPass {
    static func run(
        _ plan: NativeInputContextPlan, declarations: NativePrimitiveDeclarations, access: NativeQueryAccess
    ) throws -> NativeSampledInput {
        let op = plan.operation, binding = plan.query.binding
        guard op.clickContract != nil, op.intent == "click" else {
            throw DriverError("click offers require the negotiated click operation", code: .invalidRequest)
        }
        var input: [String: Any] = [
            "keyboardSurface": NSNull(), "focusedNode": NSNull(), "focusAncestors": [], "editor": NSNull(),
            "editorOwner": NSNull(), "selectionKnown": "unknown", "geometryCurrent": "unknown",
            "windowRoute": "unknown", "pointerRoute": "unknown"
        ]
        var enabled: Bool?, foreground = false, role: String?, complete = false, menu = false
        func pointerOffers(
            _ pointer: NativePointerUnit, at point: CGPoint, exposure: Bool = false, addressedReceiver: Bool? = nil,
            foregroundHitRequired: Bool = false,
            qualifyPopup: ((NativePointerUnit.PhysicalClickSample) -> NativePopupPointerQualification?)? = nil
        ) throws {
            try pointer.validateWindowIdentity()
            if let qualifyPopup {
                // The existing point readiness sample retains its physical hit.
                // No preparation or second directory is needed for this offer.
                let physical = pointer.physicalClickSample(at: point)
                try RequestDiagnostics.checkCancellation()
                let proof = physical.ready ? qualifyPopup(physical) : nil
                foreground = physical.foreground
                input["geometryCurrent"] = "yes"
                input["pointerRoute"] = proof == nil ? "no" : "yes"
                declarations.declareClick(.pointer(.click), offer: NativePopupClickPolicy.pointerOffer(proof))
                declarations.declareClick(
                    .pointer(.click),
                    offer: .init(
                        routeFamily: "macos.windowDirected.v1", delivery: .windowDirected, admission: .unavailable,
                        reason: "This popup choice requires its confirmed foreground pointer point.",
                        preparationOptions: []))
                return
            }
            foreground = pointer.foregroundWindow(at: point)
            input["geometryCurrent"] = "yes"
            var transport = true
            do {
                try BackgroundEvents.requirePointerRoute(window: pointer.window)
            } catch is NativePreparationUnavailable { transport = false }
            let receiver = try addressedReceiver ?? pointer.hasAddressedPointerReceiver(at: point)
            let directedAvailable = transport && receiver
            let backgroundAvailable =
                directedAvailable && !foregroundHitRequired && pointer.backgroundPreparationAvailable()
            // The same existing HID readiness query retains its provenance;
            // do not sample readiness again to manufacture a preference.
            let physical = pointer.physicalClickSample(at: point)
            let systemReady = physical.ready
            let directed = NativeClickContract(
                routeFamily: "macos.windowDirected.v1", delivery: .windowDirected,
                admission: directedAvailable
                    ? (!exposure && !foregroundHitRequired && pointer.ready(at: point, delivery: .windowDirected)
                        ? .ready : .preparationRequired) : .unavailable,
                reason: !transport
                    ? "Window-directed mouse transport is unavailable."
                    : !receiver
                        ? "The point has no specific native receiver for addressed mouse input; the physical pointer route remains available."
                        : !backgroundAvailable
                            ? "The native window needs foreground preparation before addressed mouse input." : nil,
                preparationOptions: directedAvailable
                    ? (backgroundAvailable ? ["background", "foreground"] : ["foreground"]) : [],
                backgroundFamily: "macos.windowDirected.v1", backgroundUnavailable: !backgroundAvailable)
            try RequestDiagnostics.checkCancellation()
            let system = NativeClickContract(
                routeFamily: "macos.hid.v1", delivery: .systemPointer,
                admission: systemReady && !exposure && !foregroundHitRequired ? .ready : .preparationRequired,
                reason: exposure
                    ? "The original element needs to be scrolled into view."
                    : foregroundHitRequired
                        ? "The original element hit must be checked after window preparation."
                        : systemReady ? nil : "The exact target window needs authorized foreground preparation.",
                preparationOptions: foregroundHitRequired
                    ? ["foreground"] : systemReady ? (exposure ? ["background"] : []) : ["foreground"],
                backgroundFamily: "macos.windowDirected.v1",
                backgroundUnavailable: !directedAvailable || !backgroundAvailable)
            for offer in [directed, system] { declarations.declareClick(.pointer(.click), offer: offer) }
            input["pointerRoute"] = systemReady || directed.admission == .ready ? "yes" : "no"
        }
        if plan.query.desktop {
            let pointer = try NativePointerIntent(
                unit: "pointer_sequence", kind: "click", parameters: op.request.parameters, foreground: false)
            _ = try access.units.desktopUnit(binding.wire).pointer(pointer)
            foreground = true
            input["geometryCurrent"] = "yes"
            input["pointerRoute"] = "yes"
            declarations.declareClick(
                .desktop("click"),
                offer: NativeClickContract(
                    routeFamily: "macos.hid.desktop.v1", delivery: .systemPointer, admission: .ready, reason: nil,
                    preparationOptions: []))
        } else if binding.wire["capture"] is String {
            let pointer = NativePointerUnit(capture: try access.captures.input(binding.wire).entry)
            let request = try NativePointerIntent(
                unit: "pointer_sequence", kind: "click", parameters: op.request.parameters, foreground: false)
            try pointer.validateParameters(request)
            try pointerOffers(pointer, at: pointer.point(request.start!))
        } else if let node = binding.node {
            let facts = try access.objects.nativeActionFacts(node)
            enabled = facts.enabled
            foreground = facts.foreground
            role = facts.role.flatMap { $0.isEmpty ? nil : $0 }
            complete = facts.actions != nil
            menu = facts.applicationMenuContext
            let applicationRoot = access.directory.applicationRoot(binding.wire["surface"] as? String ?? "") != nil
            var popupChoice = false
            if !applicationRoot, !menu, plan.actuator == nil, plan.receiver == nil,
                NativePopupClickPolicy.permitsIntent(op.request), let context = facts.popupContext
            {
                popupChoice = NativePopupClickPolicy.requiresPointer(
                    context: context, window: context.popup, currentWindowID: facts.popupElementWindowID, role: role,
                    enabled: facts.enabled, focusWritable: facts.focusWritable, actions: facts.actions,
                    same: { CFEqual($0, $1) })
            }
            if plan.actuator == nil {
                for action in facts.actions ?? [] {
                    let isMenu = applicationRoot
                    // NSMenu can expose a stale disabled bit until its retained
                    // document path is prepared. The menu unit owns that refresh.
                    let supported = (isMenu || facts.enabled != false) && (!menu || isMenu)
                    let family = (isMenu ? "macos.menuAction." : "macos.axAction.") + action.name
                    let readiness =
                        try isMenu
                        ? access.units.menuUnit(binding.wire, within: plan.within).readiness(enabled: enabled)
                        : NativeReadiness()
                    let documentPreparation =
                        try isMenu ? NativeActionDocumentPolicy.Preparation() : facts.documentPreparation(action.name)
                    let documentRequired = documentPreparation.required
                    let offer = NativeClickContract(
                        routeFamily: family, delivery: .objectAction,
                        admission: !supported
                            ? .unavailable
                            : (isMenu && !readiness.ready) || documentRequired ? .preparationRequired : .ready,
                        reason: supported
                            ? nil : "The selected native action is disabled or unavailable in this scope.",
                        preparationOptions: documentPreparation.foregroundOnly
                            ? ["foreground"] : isMenu || documentRequired ? ["background", "foreground"] : [],
                        backgroundFamily: !documentPreparation.foregroundOnly && (isMenu || documentRequired)
                            ? family : nil, backgroundUnavailable: documentPreparation.foregroundOnly,
                        documentForeground: isMenu ? .notRequired : facts.documentForeground(action.name))
                    declarations.declareClick(
                        isMenu
                            ? .menuAction(action.name, action.description) : .action(action.name, action.description),
                        offer: NativePopupClickPolicy.actionOffer(offer, requiresPointer: popupChoice))
                }
            }
            if !applicationRoot && !menu {
                // Independent semantic candidate: no geometry, focus or pointer
                // capability is required. V1 clients keep their existing schema.
                if op.clickContract == "v2", plan.actuator == nil, role == kAXRowRole,
                    let selection = try access.objects.nativeSelectionContract(node)
                {
                    declarations.declareClick(
                        .selection(selection),
                        offer: NativeClickContract(
                            routeFamily: selection.family, delivery: .objectSelection, admission: .ready, reason: nil,
                            preparationOptions: []))
                }
                do {
                    let unit = try access.units.elementPointerUnit(binding.wire, actuator: plan.actuator)
                    if let exposure = try unit.exposure() {
                        if popupChoice {
                            throw DriverError(
                                "the original popup choice must already be visible", code: .inputUnavailable)
                        }
                        try pointerOffers(
                            NativePointerUnit(
                                application: unit.application, window: exposure.mapping, nativeWindow: unit.window),
                            at: exposure.point, exposure: true)
                        return NativeSampledInput(
                            plan: plan, declarations: declarations, enabled: enabled, foreground: foreground,
                            input: input, nativeRole: role, actionsComplete: complete, applicationMenu: menu)
                    }
                    let sampled = try unit.observedGeometry()
                    input["geometryCurrent"] = "yes"
                    if let hit = sampled.hit {
                        if CFEqual(hit, unit.element) {
                            input["pointerHit"] = binding.node!
                        } else if let member = unit.member, CFEqual(hit, member) {
                            input["pointerHit"] = plan.actuator!.node
                        } else {
                            do {
                                input["pointerHit"] = try access.objects.registerNativeRoot(
                                    hit, app: unit.application, scope: string(binding.wire["surface"], label: "surface")
                                )
                            } catch let error as DriverError where error.code == .staleTarget {
                                input.removeValue(forKey: "pointerHit")
                            }
                        }
                    }
                    guard sampled.dispatchGeometryReady || !popupChoice && sampled.preparation != .unavailable else {
                        throw DriverError(
                            "the current hit does not confirm the original element and has no window preparation route",
                            code: .inputUnavailable)
                    }
                    let geometry = sampled.geometry
                    let pointer = NativePointerUnit(
                        application: unit.application, window: geometry.mapping, nativeWindow: unit.window)
                    if popupChoice, let context = facts.popupContext {
                        try pointerOffers(
                            pointer, at: geometry.point,
                            qualifyPopup: { physical in
                                guard let hit = physical.hit else { return nil }
                                return NativePopupPointerQualification(
                                    context: context, element: unit.element, pid: unit.application.processIdentifier,
                                    sample: .init(
                                        geometry: geometry, hit: sampled.hit, physicalHit: hit,
                                        foreground: physical.foreground), same: { CFEqual($0, $1) })
                            })
                    } else {
                        try pointerOffers(
                            pointer, at: geometry.point,
                            addressedReceiver: plan.actuator == nil && sampled.basis == .exactHit
                                ? NativeAddressedPointerReceiver(
                                    role: role, actions: facts.actions?.map { $0.name },
                                    focusWritable: facts.focusWritable
                                ).supported : nil, foregroundHitRequired: sampled.preparation == .foreground)
                    }
                } catch let error as DriverError where error.code == .inputUnavailable || error.code == .modeUnsupported
                {
                    // A pointer geometry failure cannot erase an independent
                    // action on the retained object.
                    for (family, delivery) in [
                        ("macos.windowDirected.v1", NativeClickDelivery.windowDirected),
                        ("macos.hid.v1", .systemPointer)
                    ] {
                        declarations.declareClick(
                            .pointer(.click),
                            offer: .init(
                                routeFamily: family, delivery: delivery, admission: .insufficientEvidence,
                                reason: error.description, preparationOptions: []))
                    }
                }
            }
        } else {
            throw DriverError("click requires an element or captured target", code: .invalidRequest)
        }
        return NativeSampledInput(
            plan: plan, declarations: declarations, enabled: enabled, foreground: foreground, input: input,
            nativeRole: role, actionsComplete: complete, applicationMenu: menu)
    }
}
