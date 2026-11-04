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
            _ pointer: NativePointerUnit, at point: CGPoint, exposure: Bool = false,
            addressedReceiver: NativeAddressedPointerReceiver.Qualification? = nil,
            foregroundHitRequired: Bool = false, captured: Bool = false,
            qtComboChoice: NativeQtComboChoice? = nil,
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
            let receiver: NativeAddressedPointerReceiver.Qualification
            if let addressedReceiver {
                receiver = addressedReceiver
            } else {
                receiver = try pointer.addressedPointerReceiver(at: point)
            }
            let requirement = receiver.requirement(captured: captured)
            let directedPointer = pointer.requiring(requirement ?? .contentReceiver)
            let directedAvailable = transport && requirement != nil
            let foregroundGeometryRequired = receiver.requiresForegroundGeometry
            let backgroundAvailable =
                directedAvailable && !foregroundHitRequired && !foregroundGeometryRequired
                && directedPointer.backgroundPreparationAvailable()
            // The same existing HID readiness query retains its provenance;
            // do not sample readiness again to manufacture a preference.
            let physical = pointer.physicalClickSample(at: point)
            let systemReady = physical.ready
            let directedReady = directedAvailable && !exposure && !foregroundHitRequired
                && (!foregroundGeometryRequired || foreground)
                && directedPointer.ready(at: point, delivery: .windowDirected)
            var directed = NativeClickContract(
                routeFamily: "macos.windowDirected.v1", delivery: .windowDirected,
                admission: directedAvailable
                    ? (directedReady ? .ready : .preparationRequired) : .unavailable,
                reason: !transport
                    ? "Window-directed mouse transport is unavailable."
                    : requirement == nil
                        ? "The point has no specific native receiver for addressed mouse input; the physical pointer route remains available."
                        : !backgroundAvailable && !directedReady
                            ? "The native window needs foreground preparation before addressed mouse input." : nil,
                preparationOptions: directedAvailable
                    ? (backgroundAvailable ? ["background", "foreground"] : ["foreground"]) : [],
                backgroundFamily: "macos.windowDirected.v1", backgroundUnavailable: !backgroundAvailable,
                pointerRequirement: requirement ?? .contentReceiver,
                requiresForegroundGeometry: foregroundGeometryRequired)
            try RequestDiagnostics.checkCancellation()
            var system = NativeClickContract(
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
            directed.qtComboChoice = qtComboChoice
            system.qtComboChoice = qtComboChoice
            for offer in [directed, system] { declarations.declareClick(.pointer(.click), offer: offer) }
            input["pointerRoute"] = systemReady || directed.admission == .ready ? "yes" : "no"
        }
        if plan.query.desktop {
            let pointer = try NativePointerIntent(
                unit: "pointer_sequence", kind: "click", parameters: op.request.parameters, foreground: false)
            _ = try access.units.desktopUnit(binding.wire).pointer(pointer, requireAuthorization: false)
            foreground = true
            input["geometryCurrent"] = "yes"
            input["pointerRoute"] = "yes"
            declarations.declareClick(
                .desktop("click"),
                offer: NativeClickContract(
                    routeFamily: "macos.hid.desktop.v1", delivery: .systemPointer, admission: .ready, reason: nil,
                    preparationOptions: []))
        } else if binding.wire["capture"] is String {
            let captured = try access.captures.input(binding.wire)
            let pointer = NativePointerUnit(capture: captured)
            let request = try NativePointerIntent(
                unit: "pointer_sequence", kind: "click", parameters: op.request.parameters, foreground: false)
            try pointer.validateParameters(request)
            let point = try pointer.point(request.start!)
            var choice: NativeQtComboChoice?
            let receiver = try pointer.addressedPointerReceiver(at: point) { hit, role in
                choice = try NativeQtComboChoice.captured(captured, pixels: request.start!, hit: hit, role: role)
                return choice != nil ? .declaredInteraction : nil
            }
            try pointerOffers(
                pointer, at: point, addressedReceiver: receiver,
                captured: true, qtComboChoice: choice)
        } else if let node = binding.node {
            let facts = try access.objects.nativeActionFacts(node).resolvingDocument {
                try access.scopes.fixedInputWindow(binding.wire)
            }
            enabled = facts.enabled
            foreground = facts.foreground
            role = facts.role.flatMap { $0.isEmpty ? nil : $0 }
            complete = facts.actions != nil
            menu = facts.applicationMenuContext
            let applicationRoot = access.directory.applicationRoot(binding.wire["surface"] as? String ?? "") != nil
            var popupChoice = false
            if !applicationRoot, !menu, plan.actuator == nil, plan.receiver == nil, facts.qtComboChoice == nil,
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
                    let family = (isMenu ? "macos.menuAction." : "macos.axAction.") + action.name
                    let readiness =
                        try isMenu
                        ? access.units.menuUnit(binding.wire, within: plan.within).readiness(enabled: enabled)
                        : NativeReadiness()
                    let actionUnit = NativeInputUnit.action(action.name, action.description)
                    let document = try isMenu ? nil : declarations.actionDocument(for: actionUnit, facts: facts)
                    let documentRequired: Bool
                    if let document, document.selectionRequired {
                        documentRequired = try document.observe() != .target
                    } else {
                        documentRequired = false
                    }
                    // Menu validation and ordinary controls can depend on the
                    // selected document. Admit only that independent preparation;
                    // the addressed action still checks fresh enabled at dispatch.
                    let supported = (isMenu || documentRequired || facts.enabled != false) && (!menu || isMenu)
                    let offer = NativeClickContract(
                        routeFamily: family, delivery: .objectAction,
                        admission: !supported
                            ? .unavailable
                            : (isMenu && !readiness.ready) || documentRequired ? .preparationRequired : .ready,
                        reason: supported
                            ? nil : "The selected native action is disabled or unavailable in this scope.",
                        preparationOptions: isMenu || documentRequired ? ["background", "foreground"] : [],
                        backgroundFamily: isMenu || documentRequired ? family : nil)
                    declarations.declareClick(
                        isMenu
                            ? .menuAction(action.name, action.description) : .action(action.name, action.description),
                        offer: NativeQtComboChoice.actionOffer(
                            NativePopupClickPolicy.actionOffer(
                                NativeToggleClickPolicy.actionOffer(offer, action: action.name, role: role),
                                requiresPointer: popupChoice),
                            action: action.name, kind: facts.qtComboChoice?.kind),
                        actionDocument: document)
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
                        let receiver: NativeAddressedPointerReceiver.Qualification?
                        if plan.actuator == nil, unit.member == nil {
                            receiver = unit.qtComboChoice != nil ? .declaredInteraction
                                : NativeAddressedPointerReceiver.query(
                                    unit.element, role: role, actions: facts.actions?.map { $0.name },
                                    focusWritable: facts.focusWritable).exposureQualification
                        } else { receiver = nil }
                        // The viewport center receives preparation wheels; it
                        // need not advertise the original target's click. The
                        // selected element unit rechecks that target after the
                        // scroll, including an exact hit for undeclared content.
                        try pointerOffers(
                            NativePointerUnit(
                                application: unit.application, window: exposure.mapping, nativeWindow: unit.window),
                            at: exposure.point, exposure: true, addressedReceiver: receiver, qtComboChoice: unit.qtComboChoice)
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
                        let addressedReceiver: NativeAddressedPointerReceiver.Qualification?
                        if unit.qtComboChoice != nil {
                            addressedReceiver = .declaredInteraction
                        } else if plan.actuator == nil, unit.member == nil {
                            // Only an exact hit needs semantic declarations.
                            // A preparable coarse hit retains the original
                            // geometry obligation instead of querying setters.
                            let receiver = sampled.basis == .exactHit
                                ? NativeAddressedPointerReceiver.query(
                                    unit.element, role: role, actions: facts.actions?.map { $0.name },
                                    focusWritable: facts.focusWritable)
                                : NativeAddressedPointerReceiver(role: role, actions: facts.actions?.map { $0.name })
                            addressedReceiver = receiver.retainedElementQualification(
                                    basis: sampled.basis, preparation: sampled.preparation,
                                    exactOriginalHit: sampled.hit.map { CFEqual($0, unit.element) } == true,
                                    popup: facts.popupContext != nil)
                        } else { addressedReceiver = nil }
                        try pointerOffers(
                            pointer, at: geometry.point, addressedReceiver: addressedReceiver,
                            foregroundHitRequired: sampled.preparation == .foreground, qtComboChoice: unit.qtComboChoice)
                    }
                } catch let error as DriverError where error.code == .inputUnavailable || error.code == .unsupportedOperation
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
