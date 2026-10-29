// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices

/// QUERY/RESOURCE: sample existing route facts and register observed native parts
/// in the original registry; no preparation.
@MainActor enum SampleNativeInputRoutesPass {
    static func run(
        _ plan: NativeInputContextPlan, declarations: NativePrimitiveDeclarations, access: NativeQueryAccess
    ) throws -> NativeSampledInput {
        let queryScope = plan.query, op = plan.operation
        let binding = queryScope.binding, desktop = queryScope.desktop
        let actuator = plan.actuator, receiver = plan.receiver
        func declare(
            _ unit: NativeInputUnit, supported: Bool,
            readiness: (Bool) throws -> NativeReadiness = { _ in NativeReadiness() }
        ) rethrows { try declarations.declare(unit, supported: supported, enabled: enabled, readiness: readiness) }
        let enabled: Bool?, foreground: Bool
        var input: [String: Any] = [
            "keyboardSurface": NSNull(), "focusedNode": NSNull(), "focusAncestors": [], "editor": NSNull(),
            "editorOwner": NSNull(), "selectionKnown": "unknown", "geometryCurrent": "unknown",
            "windowRoute": "unknown", "pointerRoute": "unknown"
        ]
        func recordPointer(_ unit: NativeElementPointerUnit, geometryNode: String? = nil) throws -> NativeReadiness {
            let pointer = try unit.conditions()
            input["geometryCurrent"] = pointer.geometry ? "yes" : "unknown"
            input["pointerRoute"] = pointer.geometry ? (pointer.route ? "yes" : "no") : "unknown"
            if let hit = pointer.hit {
                if CFEqual(hit, unit.element) {
                    input["pointerHit"] = geometryNode ?? binding.node!
                } else if let member = unit.member, CFEqual(hit, member) {
                    input["pointerHit"] = actuator!.node
                } else {
                    do {
                        input["pointerHit"] = try access.objects.registerNativeRoot(
                            hit, app: unit.application, scope: string(binding.wire["surface"], label: "surface"))
                    } catch let error as DriverError where error.code == .staleTarget {
                        // This optional hit disappeared while reading. It
                        // cannot invalidate a usable action on the owner.
                        input.removeValue(forKey: "pointerHit")
                    }
                }
            }
            return pointer.readiness
        }
        var nativeRole: String?, actionsComplete = false, applicationMenu = false
        // An independent object edit does not need a keyboard receiver.
        // Do not sample or prepare unrelated focus to declare it.
        // Optional keyboard receiver evidence must not erase a setter
        // addressed to this same object. A selected internal editor is
        // different: its owner must not receive the child's replacement.
        let textFacts =
            try op.intent == "replace" && actuator == nil
            ? binding.node.map { try access.objects.nativeTextValueFacts($0) } : nil
        if desktop {
            let unit = try access.units.desktopUnit(binding.wire)
            enabled = nil
            foreground = true
            if NativePointerIntent.Kind(rawValue: op.intent) != nil {
                let pointer = try NativePointerIntent(
                    unit: "pointer_sequence", kind: op.intent, parameters: op.request.parameters, foreground: false)
                _ = try unit.pointer(pointer)
                input["geometryCurrent"] = "yes"
                input["pointerRoute"] = "yes"
            } else {
                // Empty insertion is resolved by Python after this
                // scope observation, without selecting an input unit.
                if op.intent == "insert", op.request.parameters["text"] as? String == "" {
                    _ = try object(op.request.parameters, keys: ["text"])
                } else {
                    _ = try NativeKeyboardIntent(
                        unit: "key_sequence", kind: op.intent, parameters: op.request.parameters, foreground: false)
                }
                _ = try access.desktop().keyboardDestination(unit.ref)
            }
            try declare(.desktop(op.intent), supported: true)
        } else if let actuator, actuator.relation == "scrollPage" {
            let facts = try access.objects.nativeActionFacts(binding.node!)
            enabled = facts.enabled
            foreground = facts.foreground
            nativeRole = facts.role
            try declare(.scroll, supported: true)
        } else if let actuator, actuator.relation == "scrollPointer" {
            _ = try NativePointerIntent(
                unit: "pointer_sequence", kind: op.intent, parameters: op.request.parameters, foreground: false,
                element: true)
            let facts = try access.objects.nativeActionFacts(binding.node!)
            enabled = facts.enabled
            foreground = facts.foreground
            nativeRole = facts.role
            let readiness = try recordPointer(
                access.units.elementPointerUnit(binding.wire, actuator: actuator), geometryNode: actuator.region)
            declare(.pointer(.scroll), supported: true, readiness: { _ in readiness })
        } else if let actuator, actuator.relation == "controlPart" {
            let facts = try access.objects.nativeActionFacts(binding.node!)
            enabled = facts.enabled
            foreground = facts.foreground
            nativeRole = facts.role
            let readiness = try recordPointer(access.units.elementPointerUnit(binding.wire, actuator: actuator))
            declare(.pointer(.click), supported: true, readiness: { _ in readiness })
        } else if binding.wire["capture"] is String, let kind = NativePointerIntent.Kind(rawValue: op.intent) {
            let unit = NativePointerUnit(capture: try access.captures.input(binding.wire).entry)
            let request = try NativePointerIntent(
                unit: "pointer_sequence", kind: op.intent, parameters: op.request.parameters, foreground: false)
            let point = try unit.point(request.start!)
            enabled = nil
            foreground = unit.foregroundWindow(at: point)
            input["geometryCurrent"] = "yes"
            let ready = unit.ready(at: point, kind: kind)
            input["pointerRoute"] = ready ? "yes" : "no"
            var pending = [("receiver", ready ? "yes" : "no")]
            if BackgroundTarget.isExclusive { pending.append(("foreground", foreground ? "yes" : "no")) }
            declare(.pointer(kind), supported: true, readiness: { _ in NativeReadiness(pending) })
        } else if let facts = textFacts, let contract = facts.contract, contract.independent {
            enabled = facts.enabled
            foreground = facts.foreground
            try declare(.textValue(contract), supported: true)
        } else if let kind = NativeKeyboardIntent.Kind(rawValue: op.intent) {
            let unit = try access.units.keyboardUnit(binding.wire, actuator: actuator, receiver: receiver)
            let facts = try unit.sample(includeAncestors: true)
            let cellActivation = try kind == .replace && unit.requiresCellEditorActivation()
            enabled = unit.element.flatMap { AXAccess.read($0, kAXEnabledAttribute).value as? Bool }
            foreground = facts.foreground
            nativeRole = unit.element.flatMap { AXAccess.read($0, kAXRoleAttribute).value as? String }
            if let contract = textFacts?.contract { try declare(.textValue(contract), supported: true) }
            input["windowRoute"] = facts.windowRoute
            if facts.windowRoute == "yes", facts.scopeRoute == "yes" {
                input["keyboardSurface"] = binding.wire["surface"]
            }
            func register(_ element: AXUIElement) throws -> String {
                try access.objects.registerNativeRoot(
                    element, app: unit.application, scope: string(binding.wire["surface"], label: "surface"))
            }
            // Preserve a provider's coarse focus report for Python's
            // semantic lookup. This is not receiver readiness: the
            // native unit still requires exact focus or a selected,
            // live-checked relationship before sending input.
            let references = try NativeReceiverEvidence.focusReferences(
                reportedFocus: facts.reportedFocus, ancestors: facts.ancestors, register: register,
                onUnavailable: { part, error in
                    diagnose("input focus observation unavailable (\(part)): \(error.description)")
                })
            input["focusedNode"] = references.focus as Any? ?? NSNull()
            input["focusAncestors"] = references.ancestors
            // No change to facts.focusReportAbsent, scope/window routes or
            // primitive readiness: an unavailable reference is not evidence of
            // no competing receiver. Selected native units recheck the target
            // and actual receiver before preparation and every dispatch.
            if let actuator {
                input["editor"] = actuator.node
                input["editorOwner"] = binding.node!
            }
            if !cellActivation, [.focus, .insert, .replace, .paste].contains(kind), let element = unit.element,
                [kAXTextFieldRole, kAXTextAreaRole, "AXWebArea"].contains(nativeRole ?? "")
            {
                let pointer = NativePointerFocusUnit(
                    application: unit.application, window: unit.window, element: element,
                    receiverRelation: unit.receiverRelation, cellEditor: unit.cellEditor)
                let readiness = try pointer.readiness()
                // An already focused editor needs no pointer preparation.
                // Re-offering a no-op cannot establish missing editing
                // capability. Explicit focus may still confirm readiness.
                if kind == .focus || !readiness.ready {
                    declare(.pointerFocus, supported: true, readiness: { _ in readiness })
                }
            }
            if !cellActivation, [.insert, .replace, .paste].contains(kind), let element = unit.element {
                let edit = NativeSelectedTextUnit(
                    application: unit.application, window: unit.window, element: element,
                    receiverRelation: unit.receiverRelation, cellEditor: unit.cellEditor)
                if kind != .paste, let contract = edit.contract(replace: kind == .replace) {
                    try declare(.selectedText(contract), supported: true) { _ in try edit.readiness(contract) }
                } else {
                    let receiver = NativeReceiverPreparation(
                        application: unit.application, window: unit.window, element: element,
                        receiverRelation: unit.receiverRelation, cellEditor: unit.cellEditor)
                    if let contract = receiver.contract(), try !receiver.readiness().ready {
                        // This declares only AX preparation. The public
                        // resolver selected this exact candidate;
                        // reinspection must prove any editing capability.
                        try declare(.receiverContext(contract), supported: true) { _ in try receiver.readiness() }
                    }
                }
            }
            let observedInterpreter =
                try unit.textProposal(kind: kind, text: op.request.parameters["text"] as? String, state: facts)?
                .interpreter.kind ?? .noObservedWeb
            if kind == .replace {
                if let element = unit.element, let selection = NativeReplacementUnit(element: element).contract() {
                    let textPolicy = declarations.keyboardPolicy(
                        kind: kind, selection: selection, observed: observedInterpreter)
                    let keyboardSupported = try unit.textTransportSupported(
                        kind: kind, text: op.request.parameters["text"] as? String, state: facts, textPolicy: textPolicy
                    )
                    let readiness = try unit.readiness(
                        kind: kind, text: op.request.parameters["text"] as? String, replacement: selection,
                        state: facts, textPolicy: textPolicy)
                    input["selectionKnown"] = readiness.pending.contains("selection") ? "unknown" : "yes"
                    try declare(.keyboard(kind, selection, textPolicy), supported: keyboardSupported) { prepared in
                        try unit.readiness(
                            kind: kind, text: op.request.parameters["text"] as? String, replacement: selection,
                            prepared: prepared, state: facts, textPolicy: textPolicy)
                    }
                }
            } else {
                let textPolicy = declarations.keyboardPolicy(kind: kind, selection: nil, observed: observedInterpreter)
                let keyboardSupported = try unit.textTransportSupported(
                    kind: kind, text: op.request.parameters["text"] as? String, state: facts, textPolicy: textPolicy)
                try declare(.keyboard(kind, nil, textPolicy), supported: keyboardSupported) { prepared in
                    try unit.readiness(
                        kind: kind, key: op.request.parameters["key"] as? String,
                        text: op.request.parameters["text"] as? String, prepared: prepared, state: facts,
                        textPolicy: textPolicy)
                }
            }
        } else if let node = binding.node {
            let facts = try access.objects.nativeActionFacts(node)
            enabled = facts.enabled
            foreground = facts.foreground
            applicationMenu = facts.applicationMenuContext
            nativeRole = facts.role.flatMap { $0.isEmpty ? nil : $0 }
            actionsComplete = facts.actions != nil
            for action in facts.actions ?? [] {
                if access.directory.applicationRoot(binding.wire["surface"] as? String ?? "") != nil {
                    let readiness = try access.units.menuUnit(binding.wire).readiness(enabled: facts.enabled)
                    declare(
                        .menuAction(action.name, action.description), supported: true, readiness: { _ in readiness })
                } else {
                    try declare(
                        .action(action.name, action.description),
                        supported: !facts.applicationMenuContext
                            && (action.name != kAXRaiseAction || BackgroundTarget.isExclusive || facts.foreground))
                }
            }
            if op.intent == "click", access.directory.applicationRoot(binding.wire["surface"] as? String ?? "") == nil,
                !facts.applicationMenuContext
            {
                let readiness = try recordPointer(access.units.elementPointerUnit(binding.wire))
                declare(.pointer(.click), supported: true, readiness: { _ in readiness })
            }
        } else {
            throw DriverError("this intent requires an element target", code: .invalidRequest)
        }
        return NativeSampledInput(
            plan: plan, declarations: declarations, enabled: enabled, foreground: foreground, input: input,
            nativeRole: nativeRole, actionsComplete: actionsComplete, applicationMenu: applicationMenu)
    }
}
