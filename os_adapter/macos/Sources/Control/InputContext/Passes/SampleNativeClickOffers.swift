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
        func pointerOffers(_ pointer: NativePointerUnit, at point: CGPoint, exposure: Bool = false) throws {
            try pointer.validateWindowIdentity()
            foreground = pointer.foregroundWindow(at: point)
            input["geometryCurrent"] = "yes"
            var transport = true
            do {
                try BackgroundEvents.requirePointerRoute(window: pointer.window)
            } catch is NativePreparationUnavailable { transport = false }
            let directedAvailable = !BackgroundTarget.isExclusive && transport && pointer.canPrepareWindowDirected()
            let systemReady = pointer.ready(at: point, delivery: .systemPointer)
            let directed = NativeClickContract(
                routeFamily: "macos.windowDirected.v1", delivery: .windowDirected,
                admission: directedAvailable
                    ? (!exposure && pointer.ready(at: point, delivery: .windowDirected) ? .ready : .preparationRequired)
                    : .unavailable,
                reason: BackgroundTarget.isExclusive
                    ? "Exclusive mode uses desktop mouse input."
                    : !transport
                        ? "Window-directed mouse transport is unavailable."
                        : !directedAvailable
                            ? "The first mouse-down may only activate this window; foreground preparation is required."
                            : nil, preparationOptions: directedAvailable ? ["background"] : [])
            try RequestDiagnostics.checkCancellation()
            let system = NativeClickContract(
                routeFamily: "macos.hid.v1", delivery: .systemPointer,
                admission: systemReady && !exposure ? .ready : .preparationRequired,
                reason: exposure ? "The original element needs to be scrolled into view."
                    : systemReady ? nil : "The exact target window needs authorized foreground preparation.",
                preparationOptions: systemReady ? (exposure ? ["background"] : []) : ["foreground"],
                backgroundFamily: "macos.windowDirected.v1",
                backgroundUnavailable: !transport || !directedAvailable)
            for offer in foreground ? [system, directed] : [directed, system] {
                declarations.declareClick(.pointer(.click), offer: offer)
            }
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
            if plan.actuator == nil {
                for action in facts.actions ?? [] {
                    let isMenu = applicationRoot
                    // NSMenu can expose a stale disabled bit until its retained
                    // document path is prepared. The menu unit owns that refresh.
                    let supported =
                        (isMenu || facts.enabled != false) && (!menu || isMenu)
                        && (action.name != kAXRaiseAction || BackgroundTarget.isExclusive || foreground)
                    let family = (isMenu ? "macos.menuAction." : "macos.axAction.") + action.name
                    let readiness =
                        try isMenu
                        ? access.units.menuUnit(binding.wire, within: plan.within).readiness(enabled: enabled)
                        : NativeReadiness()
                    let documentRequired =
                        try !isMenu && action.name == kAXPressAction && facts.document?.focus() == .other
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
                        offer: offer)
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
                        try pointerOffers(
                            NativePointerUnit(
                                application: unit.application, window: exposure.mapping, nativeWindow: unit.window),
                            at: exposure.point, exposure: true)
                        return NativeSampledInput(
                            plan: plan, declarations: declarations, enabled: enabled, foreground: foreground, input: input,
                            nativeRole: role, actionsComplete: complete, applicationMenu: menu)
                    }
                    let sampled = try unit.observedGeometry(allowWindowPreparation: true)
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
                    guard let geometry = sampled.geometry else {
                        throw DriverError(
                            "the element's center is not confirmed as this native hit target", code: .inputUnavailable)
                    }
                    try pointerOffers(
                        NativePointerUnit(
                            application: unit.application, window: geometry.mapping, nativeWindow: unit.window),
                        at: geometry.point)
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
