// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices

/// QUERY/RESOURCE: sample existing route facts and register observed native parts
/// in the original registry; no preparation.
@MainActor enum SampleNativeInputRoutesPass {
    static func keyboardUnit(_ plan: NativeInputContextPlan, units: any NativeInputUnitAccess) throws
        -> NativeKeyboardUnit
    {
        if let scope = plan.query.windowKeyboardScope {
            // The fixed window was resolved by this query. The caller still
            // samples its current focus and input readiness below.
            return NativeKeyboardUnit(
                application: scope.application, window: try scope.requireWindow(), element: nil, windowCommandScope: true,
                boundaryCoverageComplete: scope.windowEvidence?.coverageComplete == true)
        }
        return try units.keyboardUnit(plan.query.binding.wire, actuator: plan.actuator, receiver: plan.receiver)
    }

    static func run(
        _ plan: NativeInputContextPlan, declarations: NativePrimitiveDeclarations, access: NativeQueryAccess
    ) throws -> NativeSampledInput {
        let queryScope = plan.query, op = plan.operation
        let binding = queryScope.binding
        let actuator = plan.actuator, receiver = plan.receiver
        let originalAddressedElement = actuator == nil && receiver == nil && binding.node != nil
            && binding.node == op.target["node"] as? String && !(binding.wire["capture"] is String)
        let requestedText = op.request.parameters["text"] as? String ?? ""
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
        let recordPointer: @MainActor (NativeElementPointerUnit, String?) throws -> NativeReadiness = { unit, geometryNode in
            let pointer = try unit.conditions()
            input["geometryCurrent"] = pointer.geometry ? "yes" : "unknown"
            input["pointerRoute"] = pointer.geometry ? (pointer.route ? "yes" : "no") : "unknown"
            if let hit = pointer.hit {
                if CFEqual(hit, unit.element) {
                    if let geometryNode {
                        input["pointerHit"] = geometryNode
                    } else {
                        input["pointerHit"] = binding.node!
                    }
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
        var webTypingRefusal: String?
        // An independent object edit does not need a keyboard receiver.
        // Do not sample or prepare unrelated focus to declare it.
        // Optional keyboard receiver evidence must not erase a setter
        // addressed to this same object. A selected internal editor is
        // different: its owner must not receive the child's replacement.
        let textFacts =
            try op.intent == "replace" && actuator == nil
            ? binding.node.map {
                try access.objects.nativeTextValueFacts(
                    $0, replacementText: originalAddressedElement ? requestedText : "",
                    originalWindow: { try access.scopes.fixedInputWindow(binding.wire)?.window })
            } : nil
        if let proof = textFacts?.geckoProvider { op.retainGeckoTextProvider(proof) }
        let valueContract = textFacts?.contract.flatMap { contract -> NativeElementControl.TextValueContract? in
            if case .geckoAtomicFullReplacement(let proof) = contract.semantics, proof != op.geckoTextProvider {
                return nil
            }
            return contract
        }
        if let actuator, actuator.relation == "scrollPage" {
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
                access.units.elementPointerUnit(binding.wire, actuator: actuator), actuator.region)
            declare(.pointer(.scroll), supported: true, readiness: { _ in readiness })
        } else if let actuator, actuator.relation == "controlPart" {
            let facts = try access.objects.nativeActionFacts(binding.node!)
            enabled = facts.enabled
            foreground = facts.foreground
            nativeRole = facts.role
            let readiness = try recordPointer(access.units.elementPointerUnit(binding.wire, actuator: actuator), nil)
            declare(.pointer(.click), supported: true, readiness: { _ in readiness })
        } else if binding.wire["capture"] is String, let kind = NativePointerIntent.Kind(rawValue: op.intent) {
            let unit = NativePointerUnit(capture: try access.captures.input(binding.wire))
            let request = try NativePointerIntent(
                unit: "pointer_sequence", kind: op.intent, parameters: op.request.parameters, foreground: false)
            let point = try unit.point(request.start!)
            enabled = nil
            foreground = unit.foregroundWindow(at: point)
            input["geometryCurrent"] = "yes"
            let ready = unit.ready(at: point, kind: kind)
            input["pointerRoute"] = ready ? "yes" : "no"
            let pending = [("receiver", ready ? "yes" : "no")]
            declare(.pointer(kind), supported: true, readiness: { _ in NativeReadiness(pending) })
        } else if let facts = textFacts, let contract = valueContract, contract.independent {
            enabled = facts.enabled
            foreground = facts.foreground
            try declare(.textValue(contract), supported: true)
        } else if op.intent == "key", binding.node == nil, binding.wire["rootWindow"] is NSNull,
            binding.wire["capture"] is NSNull, plan.within.isEmpty, actuator == nil, receiver == nil,
            let unit = try access.units.applicationKeyboardUnit(binding.wire)
        {
            enabled = nil
            foreground = unit.isForeground()
            applicationMenu = true
            if foreground, let key = op.request.parameters["key"] as? String, let command = try unit.command(key) {
                try declare(.applicationCommand(command), supported: true)
            }
        } else if let kind = NativeKeyboardIntent.Kind(rawValue: op.intent) {
            var unit = try keyboardUnit(plan, units: access.units)
            unit.originalAddressedElement = originalAddressedElement
            let facts = try unit.sample(includeAncestors: true)
            let cellActivation = try kind == .replace && unit.requiresCellEditorActivation()
            let enabledRead = unit.element.map { AXAccess.read($0, kAXEnabledAttribute) }
            enabled = enabledRead?.value as? Bool
            foreground = facts.foreground
            nativeRole = unit.element.flatMap { AXAccess.read($0, kAXRoleAttribute).value as? String }
            if let contract = valueContract { try declare(.textValue(contract), supported: true) }
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
            if !cellActivation, let element = unit.element,
                NativePointerFocusUnit.supportsEditorPreparation(role: nativeRole, intent: kind)
            {
                let pointer = NativePointerFocusUnit(
                    application: unit.application, window: unit.window, element: element,
                    receiverRelation: unit.receiverRelation, cellEditor: unit.cellEditor)
                let readiness = try pointer.readiness()
                // An already focused editor needs no pointer preparation.
                // Re-offering a no-op cannot establish missing editing
                // capability. Explicit focus may still confirm readiness.
                if kind == .focus || !readiness.ready {
                    declare(.pointerFocus(.editor), supported: true, readiness: { _ in readiness })
                }
            }
            if !cellActivation, kind == .replace, let element = unit.element, facts.receiver(element) != "yes",
                nativeRole == kAXIncrementorRole
            {
                let pointer = NativePointerFocusUnit(
                    application: unit.application, window: unit.window, element: element,
                    receiverRelation: unit.receiverRelation, cellEditor: unit.cellEditor, targetKind: .scalarText)
                let readiness = try pointer.readiness()
                if readiness.preparationAllowed {
                    declare(.pointerFocus(.scalarText), supported: true, readiness: { _ in readiness })
                }
            }
            if kind == .insert, nativeRole == kAXCellRole, actuator == nil, receiver == nil,
                let node = binding.node, node == op.target["node"] as? String, let element = unit.element,
                let selection = try NativeCellSelectionPreparation(
                    application: unit.application, window: unit.window, element: element).offer()
            {
                declare(.cellSelection(selection), supported: true) { _ in
                    NativeReadiness([("receiver", "unknown")])
                }
            }
            let geckoTextProvider: NativeGeckoTextProvider.Proof?
            if originalAddressedElement, !cellActivation, !requestedText.isEmpty,
                kind == .insert, let element = unit.element
            {
                let observed = try NativeGeckoTextProvider.prove(
                    element: element, presenterPID: unit.application.processIdentifier, originalWindow: { unit.window })
                if let observed { op.retainGeckoTextProvider(observed) }
                geckoTextProvider = observed == op.geckoTextProvider ? observed : nil
            } else {
                geckoTextProvider = textFacts?.geckoProvider == op.geckoTextProvider ? textFacts?.geckoProvider : nil
            }
            if !cellActivation, [.insert, .replace, .paste].contains(kind), let element = unit.element {
                let edit = NativeSelectedTextUnit(
                    application: unit.application, window: unit.window, element: element,
                    receiverRelation: unit.receiverRelation, cellEditor: unit.cellEditor)
                let providerAvailable = !originalAddressedElement || op.geckoTextProvider == nil || geckoTextProvider != nil
                if kind != .paste, providerAvailable, let contract = try edit.contract(
                    replace: kind == .replace, text: requestedText, geckoProvider: geckoTextProvider,
                    pinnedInsertionSelection: op.keyboardInsertionSelection)
                {
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
            func keyboardEntry() -> NativeKeyboardTextEntry? {
                guard unit.originalAddressedElement, !cellActivation, let element = unit.element, let enabledRead else {
                    return nil
                }
                return NativeKeyboardTextEntry.read(role: nativeRole, enabled: enabledRead) {
                    AXAccess.read(element, $0)
                }
            }
            if kind == .replace {
                if let element = unit.element,
                    let selection = NativeReplacementUnit(element: element).contract()
                        ?? keyboardEntry().flatMap({
                            $0.isPlainTextControl && $0.selection(element) != nil ? .keyboardSelectAll($0) : nil
                        })
                {
                    let textPolicy = declarations.keyboardPolicy(
                        kind: kind, selection: selection, observed: observedInterpreter)
                    let keyboardSupported = try unit.textTransportSupport(
                        kind: kind, text: op.request.parameters["text"] as? String, state: facts, textPolicy: textPolicy
                    ).supported
                    let readiness = try unit.readiness(
                        kind: kind, text: op.request.parameters["text"] as? String, replacement: selection,
                        state: facts, textPolicy: textPolicy)
                    input["selectionKnown"] = readiness.pending.contains("selection") ? "unknown" : "yes"
                    // A ready scalar can own its complete text protocol even
                    // when its AX tree also exposes a child editor.
                    let selected = originalAddressedElement && keyboardSupported && readiness.ready
                        && facts.receiver(element) == "yes"
                        ? NativeReplacementUnit(element: element).structuredScalarShortcut(selection) : selection
                    try declare(.keyboard(kind, selected, textPolicy), supported: keyboardSupported) { prepared in
                        try unit.readiness(
                            kind: kind, text: op.request.parameters["text"] as? String, replacement: selected,
                            prepared: prepared, state: facts, textPolicy: textPolicy)
                    }
                }
            } else {
                if kind == .insert, let element = unit.element, let entry = keyboardEntry() {
                    let provider = entry.isPlainTextControl ? geckoTextProvider : nil
                    let providerSelection = provider == nil ? nil : entry.selection(element)
                    let useQualifiedInsertion = provider != nil && NativeKeyboardUnit.needsGeckoInsertion(
                        selection: providerSelection, retained: op.keyboardInsertionSelection)
                    if useQualifiedInsertion || op.keyboardInsertionSelection != nil
                        || (AXAccess.read(element, "AXEditable").value as? Bool != true
                            && !(entry.isPlainTextControl
                                && AXAccess.writable(element, kAXFocusedAttribute).value == true))
                    {
                        let selection = provider == nil ? entry.selection(element) : providerSelection
                        // A composite without its own text protocol can still
                        // expose a separate editor through receiver resolution.
                        if selection != nil || entry.isPlainTextControl || op.keyboardInsertionSelection != nil {
                            // Preparation may establish the editor's actual caret.
                            // Pin a selection only on an already confirmed receiver.
                            if let selection, unit.exactTextEntryReceiver(facts) == "yes" {
                                op.retainKeyboardInsertionSelection(selection)
                            }
                            unit.keyboardInsertion = entry
                            unit.insertionSelection = op.keyboardInsertionSelection
                            if useQualifiedInsertion { unit.geckoInsertion = provider }
                        }
                    }
                }
                let textPolicy = declarations.keyboardPolicy(kind: kind, selection: nil, observed: observedInterpreter)
                let keyboardSupport = try unit.textTransportSupport(
                    kind: kind, text: op.request.parameters["text"] as? String, state: facts, textPolicy: textPolicy)
                if kind == .insert, let focus = facts.focus,
                    binding.node == nil || (originalAddressedElement && unit.element.map { CFEqual($0, focus) } == true)
                {
                    webTypingRefusal = keyboardSupport.webRefusal
                }
                try declarations.declare(
                    .keyboard(kind, nil, textPolicy),
                    supported: keyboardSupport.supported
                        && (op.keyboardInsertionSelection == nil || unit.keyboardInsertion != nil),
                    enabled: enabled, keyboardInsertion: unit.keyboardInsertion, geckoKeyboardInsertion: unit.geckoInsertion
                ) { prepared in
                    try unit.readiness(
                        kind: kind, key: op.request.parameters["key"] as? String,
                        text: op.request.parameters["text"] as? String, prepared: prepared, state: facts,
                        textPolicy: textPolicy)
                }
            }
        } else if let node = binding.node {
            var facts: NativeElementControl.ActionFacts
            if op.intent == "action" {
                let request = try NativeObjectIntent(
                    unit: "native_action", kind: "action", parameters: op.request.parameters)
                facts = try access.objects.nativeExplicitActionFacts(node, request: request.value!)
            } else {
                facts = try access.objects.nativeActionFacts(node)
            }
            facts = try facts.resolvingDocument { try access.scopes.fixedInputWindow(binding.wire) }
            enabled = facts.enabled
            foreground = facts.foreground
            applicationMenu = facts.applicationMenuContext
            nativeRole = facts.role.flatMap { $0.isEmpty ? nil : $0 }
            actionsComplete = op.intent != "action" && facts.actions != nil
            for action in facts.actions ?? [] {
                if access.directory.applicationRoot(binding.wire["surface"] as? String ?? "") != nil {
                    let readiness = try access.units.menuUnit(binding.wire).readiness(enabled: facts.enabled)
                    declare(
                        .menuAction(action.name, action.description), supported: true, readiness: { _ in readiness })
                } else {
                    let unit = NativeInputUnit.action(action.name, action.description)
                    let document = try declarations.actionDocument(for: unit, facts: facts)
                    try declarations.declare(
                        unit, supported: !facts.applicationMenuContext, enabled: enabled, actionDocument: document
                    ) { _ in
                        guard let document, document.selectionRequired else { return NativeReadiness() }
                        return try NativeReadiness([("receiver", document.observe() == .target ? "yes" : "unknown")])
                    }
                }
            }
            if op.intent == "click", access.directory.applicationRoot(binding.wire["surface"] as? String ?? "") == nil,
                !facts.applicationMenuContext
            {
                let readiness = try recordPointer(access.units.elementPointerUnit(binding.wire), nil)
                declare(.pointer(.click), supported: true, readiness: { _ in readiness })
            }
        } else {
            throw DriverError("this intent requires an element target", code: .invalidRequest)
        }
        return NativeSampledInput(
            plan: plan, declarations: declarations, enabled: enabled, foreground: foreground, input: input,
            nativeRole: nativeRole, actionsComplete: actionsComplete, applicationMenu: applicationMenu,
            webTypingRefusal: webTypingRefusal)
    }
}
