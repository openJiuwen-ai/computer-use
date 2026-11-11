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
        @discardableResult func pointerOffers(
            _ pointer: NativePointerUnit, at point: CGPoint, exposure: Bool = false,
            addressedReceiver: NativeAddressedPointerReceiver.Qualification? = nil,
            foregroundHitRequired: Bool = false, captured: Bool = false,
            requiresExactElementHit: Bool = false,
            qtComboChoice: NativeQtComboChoice? = nil
        ) throws -> Bool {
            try pointer.validateWindowIdentity()
            foreground = pointer.foregroundWindow(at: point)
            input["geometryCurrent"] = "yes"
            var transport = true
            do {
                try BackgroundEvents.requirePointerRoute(window: pointer.window)
            } catch is NativePreparationUnavailable { transport = false }
            let receiver = try addressedReceiver ?? pointer.addressedPointerReceiver(at: point)
            // A retained point/element already carries the geometry and target
            // checks used by physical input. Missing AX action declarations
            // require foreground preparation, not the user's physical cursor.
            let declared = receiver.requirement(captured: captured)
            let requirement = declared ?? (captured ? .foregroundWindowPoint : .contentReceiver)
            let foregroundGeometry = receiver.requiresForegroundGeometry || (!captured && declared == nil)
            let directedPointer = pointer.requiring(foregroundGeometry ? .foregroundWindowPoint : requirement)
            let backgroundAvailable = transport && declared != nil && !foregroundHitRequired
                && !foregroundGeometry && directedPointer.backgroundPreparationAvailable()
            let ready = transport && !exposure && !foregroundHitRequired
                && (!foregroundGeometry || foreground)
                && directedPointer.ready(at: point, delivery: .windowDirected)
            var offer = NativeClickContract(
                routeFamily: "macos.windowDirected.v1", delivery: .windowDirected,
                admission: transport ? (ready ? .ready : .preparationRequired) : .unavailable,
                reason: !transport ? "Window-directed mouse transport is unavailable."
                    : ready ? nil : "The original window or element needs preparation for directed mouse input.",
                preparationOptions: transport ? (backgroundAvailable ? ["background", "foreground"] : ["foreground"]) : [],
                backgroundFamily: "macos.windowDirected.v1", backgroundUnavailable: !backgroundAvailable,
                pointerRequirement: requirement, requiresForegroundGeometry: foregroundGeometry,
                requiresExactElementHit: requiresExactElementHit)
            offer.qtComboChoice = qtComboChoice
            declarations.declareClick(.pointer(.click), offer: offer)
            input["pointerRoute"] = ready ? "yes" : "no"
            return ready
        }
        if binding.wire["capture"] is String {
            let captured = try access.captures.input(binding.wire)
            let pointer = NativePointerUnit(capture: captured)
            let request = try NativePointerIntent(
                unit: "pointer_sequence", kind: "click", parameters: op.request.parameters, foreground: false)
            try pointer.validateParameters(request)
            let point = try pointer.point(request.start!)
            if let press = try NativeCoordinateMenuPress.match(
                intent: op.request, captured: captured, point: point, attempt: op.coordinateMenuPressAttempt)
            {
                declarations.declareClick(.action(kAXPressAction, nil), offer: press.offer)
                return NativeSampledInput(
                    plan: plan, declarations: declarations, enabled: nil, foreground: false,
                    input: input, nativeRole: nil, actionsComplete: false, applicationMenu: false)
            }
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
            let actionOffers: @MainActor (Bool) throws -> Void = { preferOpaqueRowPointer in
                guard plan.actuator == nil else { return }
                let isMenu = applicationRoot
                if isMenu, role == kAXMenuItemRole, facts.actions?.contains(where: { $0.name == kAXPressAction }) == true,
                    NativePopupClickPolicy.permitsIntent(op.request), plan.receiver == nil
                {
                    let unit = try access.units.menuUnit(binding.wire, within: plan.within)
                    if NativeMenuCommandPolicy.requiresPointer(unit, action: kAXPressAction, role: role) {
                        let ready = try foreground && unit.receiverReady()
                        let offer = NativeMenuCommandPolicy.offer(
                            ready: ready, available: BackgroundEvents.capabilities.windowDirectedTransport,
                            canPrepare: unit.window != nil)
                        declarations.declareClick(.pointer(.click), offer: offer)
                        return
                    }
                }
                for action in facts.actions ?? [] where action.permitsInferredClick {
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
                    let enabled = facts.enabled != false
                    let supported = (isMenu || documentRequired || enabled) && (!menu || isMenu)
                    let ordinaryOffer = NativeClickContract(
                        routeFamily: family, delivery: .objectAction,
                        admission: !supported
                            ? .unavailable
                            : (isMenu && !readiness.ready) || documentRequired ? .preparationRequired : .ready,
                        reason: supported
                            ? nil : "The selected native action is disabled or unavailable in this scope.",
                        preparationOptions: isMenu || documentRequired ? ["background", "foreground"] : [],
                        backgroundFamily: isMenu || documentRequired ? family : nil)
                    let offer = NativeOpaqueRowClick.actionOffer(ordinaryOffer, preferPointer: preferOpaqueRowPointer)
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
            let continuesReveal = op.requiresRevealedLeafHit(binding.wire, within: plan.within)
            var preferOpaqueRowPointer = false
            if !applicationRoot && !menu {
                // Independent semantic candidate: no geometry, focus or pointer
                // capability is required. V1 clients keep their existing schema.
                if !continuesReveal, op.clickContract == "v2", plan.actuator == nil, role == kAXRowRole,
                    let selection = try access.objects.nativeSelectionContract(node)
                {
                    declarations.declareClick(
                        .selection(selection),
                        offer: NativeClickContract(
                            routeFamily: selection.family, delivery: .objectSelection, admission: .ready, reason: nil,
                            preparationOptions: []))
                }
                let sampleElementPointer: @MainActor () throws -> Bool = {
                    let unit = try access.units.elementPointerUnit(binding.wire, actuator: plan.actuator)
                    if continuesReveal {
                        guard plan.actuator == nil, plan.receiver == nil, unit.qtComboChoice == nil,
                            facts.popupContext == nil
                        else {
                            throw DriverError("the revealed original element's route changed", code: .inputUnavailable)
                        }
                        let exact = unit.requiring(.contentReceiver, exactElementHit: true)
                        let geometry = try exact.geometry()
                        let pointer = exact.pointerUnit(mapping: geometry.mapping)
                        try BackgroundEvents.requirePointerRoute(window: geometry.mapping)
                        foreground = pointer.foregroundWindow(at: geometry.point)
                        let ready = pointer.ready(at: geometry.point, delivery: .windowDirected)
                        input["geometryCurrent"] = "yes"
                        input["pointerHit"] = node
                        input["pointerRoute"] = ready ? "yes" : "no"
                        declarations.declareClick(.pointer(.click), offer: NativeClickClippedLeaf.continuationOffer(ready: ready))
                        return false
                    }
                    let viewport = try unit.viewport()
                    if let exposure = viewport.exposure {
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
                        return false
                    }
                    let sampled = try unit.observedGeometry(in: viewport)
                    if plan.actuator?.relation == "controlPart", sampled.basis != .exactHit {
                        throw DriverError("the current point does not hit the selected control part", code: .inputUnavailable)
                    }
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
                    if !popupChoice, facts.popupContext == nil, plan.actuator == nil, plan.receiver == nil,
                        !op.didRevealClippedClickLeaf, NativePopupClickPolicy.permitsIntent(op.request),
                        let proof = try NativeClickClippedLeaf.sample(unit, hit: sampled)
                    {
                        declarations.declareClick(.pointer(.click), offer: proof.offer)
                        input["pointerRoute"] = "no"
                        return false
                    }
                    let hasPreparationRoute = sampled.preparation != .unavailable
                    guard sampled.dispatchGeometryReady || !popupChoice && hasPreparationRoute else {
                        throw DriverError(
                            "the current hit does not confirm the original element and has no window preparation route",
                            code: .inputUnavailable)
                    }
                    let geometry = sampled.geometry
                    let pointer = NativePointerUnit(
                        application: unit.application, window: geometry.mapping, nativeWindow: unit.window)
                    if popupChoice, let context = facts.popupContext {
                        // Use the existing readiness sample and its physical hit.
                        try pointer.validateWindowIdentity()
                        let physical = pointer.physicalClickSample(at: geometry.point)
                        try RequestDiagnostics.checkCancellation()
                        var proof: NativePopupPointerQualification?
                        if physical.ready, let hit = physical.hit {
                            let correctWindow = facts.popupElementWindowID == context.ownerWindowID
                            let enabled = facts.enabled != false
                            if context.kind != .submenu || correctWindow && enabled {
                                proof = NativePopupPointerQualification(
                                    context: context, element: unit.element, pid: unit.application.processIdentifier,
                                    sample: .init(
                                        geometry: geometry, hit: sampled.hit, physicalHit: hit,
                                        foreground: physical.foreground), same: { CFEqual($0, $1) })
                            }
                        }
                        foreground = physical.foreground
                        input["geometryCurrent"] = "yes"
                        input["pointerRoute"] = proof == nil ? "no" : "yes"
                        declarations.declareClick(.pointer(.click), offer: NativePopupClickPolicy.pointerOffer(proof))
                    } else {
                        let opaqueRowContent: Bool
                        if plan.actuator == nil, plan.receiver == nil, facts.popupContext == nil, sampled.basis == .exactHit,
                            let hit = sampled.hit, CFEqual(hit, unit.element), NativePopupClickPolicy.permitsIntent(op.request)
                        {
                            opaqueRowContent = try NativeOpaqueRowClick.provesPointerReceiver(
                                unit, role: role, enabled: facts.enabled, focusWritable: facts.focusWritable,
                                actions: facts.actions)
                        } else {
                            opaqueRowContent = false
                        }
                        let addressedReceiver: NativeAddressedPointerReceiver.Qualification?
                        if unit.qtComboChoice != nil || opaqueRowContent {
                            addressedReceiver = .declaredInteraction
                        } else if role == kAXStaticTextRole, let member = unit.member,
                            plan.actuator?.relation == "controlPart"
                        {
                            let exactMemberHit: Bool
                            if sampled.basis == .exactHit, let hit = sampled.hit {
                                exactMemberHit = CFEqual(hit, member)
                            } else {
                                exactMemberHit = false
                            }
                            addressedReceiver = NativeAddressedPointerReceiver.compoundTextContextQualification(
                                intent: op.request, ownerRole: role, ownerActions: facts.actions?.map(\.name),
                                memberRole: AXAccess.read(member, kAXRoleAttribute).value as? String,
                                exactMemberHit: exactMemberHit)
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
                        let ready = try pointerOffers(
                            pointer, at: geometry.point, addressedReceiver: addressedReceiver,
                            foregroundHitRequired: sampled.preparation == .foreground,
                            requiresExactElementHit: opaqueRowContent, qtComboChoice: unit.qtComboChoice)
                        return ready && opaqueRowContent
                    }
                    return false
                }
                do {
                    preferOpaqueRowPointer = try sampleElementPointer()
                } catch let error as DriverError where error.code == .inputUnavailable || error.code == .unsupportedOperation
                {
                    // A pointer geometry failure cannot erase an independent
                    // action on the retained object.
                    declarations.declareClick(
                        .pointer(.click),
                        offer: .init(
                            routeFamily: "macos.windowDirected.v1", delivery: .windowDirected,
                            admission: .insufficientEvidence, reason: error.description, preparationOptions: []))
                }
            }
            if !continuesReveal { try actionOffers(preferOpaqueRowPointer) }
        } else {
            throw DriverError("click requires an element or captured target", code: .invalidRequest)
        }
        return NativeSampledInput(
            plan: plan, declarations: declarations, enabled: enabled, foreground: foreground, input: input,
            nativeRole: role, actionsComplete: complete, applicationMenu: menu)
    }
}
