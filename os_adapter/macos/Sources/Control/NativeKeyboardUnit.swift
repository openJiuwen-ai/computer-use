// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices

/// One fixed scope and one typed keyboard delivery. This unit follows only
/// proved native text continuations within that scope.
/// Python selects preparation; every dispatch samples the receiver again.
@MainActor struct NativeKeyboardUnit {
    let application: NSRunningApplication
    let window: AXUIElement
    let element: AXUIElement?
    var receiverRelation: NativeReceiverRelation? = nil
    var cellEditorState: NativeCellEditorState? = nil
    var cellEditor: NativeCellEditorHandoff? = nil
    /// Original App/Window scope, independent of a current-forward actuator.
    var windowCommandScope = false
    /// A window projection is useful only when this unit's full scope
    /// resolved without a known coverage gap.
    var boundaryCoverageComplete = false
    /// Only the qualified dispatch copy compares this immutable original ID.
    var dispatchExpectedNativeID: CGWindowID? = nil
    var keyboardInsertion: NativeKeyboardTextEntry? = nil
    var geckoInsertion: NativeGeckoTextProvider.Proof? = nil
    var insertionSelection: NativeTextSelection? = nil
    var originalAddressedElement = false

    typealias State = NativeReceiverSnapshot
    enum ReadinessUse { case admission, receiver }

    private var background: BackgroundTarget { BackgroundTarget(application) }

    private func checkEnabled() throws {
        if let element, AXAccess.read(element, kAXEnabledAttribute).value as? Bool == false {
            throw DriverError("the selected keyboard target is disabled", code: .inputUnavailable)
        }
    }

    func sample(includeAncestors: Bool = false) throws -> State {
        let context = try NativeReceiverSnapshot.read(
            application: application, window: window, receiverRelation: receiverRelation,
            includeAncestors: includeAncestors)
        let ownedEditor =
            try cellEditor.map { proof in
                try proof.association(context).map { owned in element.map { CFEqual($0, owned.editor) } == true }
                    == true
            } ?? true
        var localReceiverConfirmed = false
        let deliveryReady = try NativeReadiness.probeRoute(check: RequestDiagnostics.checkCancellation) {
            let mapping = try NativeWindowUnit(application: application, window: context.window ?? window).mapping()
            if let expected = dispatchExpectedNativeID, mapping.id != expected {
                throw DriverError("original keyboard native window changed", code: .staleTarget)
            }
            let keyboardWindow = context.window ?? window
            let focusedWindow = context.focusedWindowRead?.value.flatMap { value -> AXUIElement? in
                guard CFGetTypeID(value) == AXUIElementGetTypeID() else { return nil }
                return unsafeDowncast(value, to: AXUIElement.self)
            }
            localReceiverConfirmed = NativeKeyboardReceiverReadiness.confirmedLocal(.init(
                windowRoute: context.windowRoute, scopeRoute: context.scopeRoute,
                concreteFocus: context.focus.map { !CFEqual($0, keyboardWindow) } == true,
                focusedWindowMatches: focusedWindow.map { CFEqual($0, keyboardWindow) } == true,
                focusOwnerMatches: context.rawFocusOwner.map { CFEqual($0, keyboardWindow) } == true,
                locallyFocused: context.rawLocalFocus,
                actualPID: context.focus.flatMap(BackgroundEvents.accessibilityProcessID),
                presenterPID: application.processIdentifier, nativeWindowKnown: mapping.id != 0))
            return localReceiverConfirmed || BackgroundEvents.keyboardPreparationIsCurrent(background, window: mapping)
        }
        return State(
            windowRoute: context.windowRoute == "yes" && !deliveryReady ? "no" : context.windowRoute,
            // Incomplete current association grants no Element receiver. Keep
            // window preparation available; only a later fresh proof can admit
            // focus on this editor or requested input after that preparation.
            focus: ownedEditor ? context.focus : nil, ancestors: ownedEditor ? context.ancestors : [],
            foreground: context.foreground, relatedEditor: ownedEditor ? context.relatedEditor : nil,
            window: context.window, surface: context.surface, scopeRoute: context.scopeRoute,
            reportedFocus: context.reportedFocus, focusReportAbsent: ownedEditor && context.focusReportAbsent,
            focusedWindowRead: context.focusedWindowRead, focusedWindowStatus: context.focusedWindowStatus,
            rawFocus: context.rawFocus, rawFocusOwner: context.rawFocusOwner, rawLocalFocus: context.rawLocalFocus,
            localReceiverConfirmed: localReceiverConfirmed)
    }

    func textProposal(
        kind: NativeKeyboardIntent.Kind, text: String?, state: State,
        textPolicy: NativeKeyboardInterpreterEvidence.Policy = .existing
    ) throws -> NativeKeyboardDeliveryProposal? {
        guard NativeKeyboardInterpreterEvidence.needsTextTransport(kind: kind.rawValue, text: text) else { return nil }
        let editor =
            element.flatMap { CFEqual($0, window) ? nil : $0 } ?? state.relatedEditor ?? state.focus ?? state.rawFocus
        let observation = try NativeKeyboardDeliveryProposal.read(
            element: editor, window: state.window ?? window, pid: application.processIdentifier, policy: textPolicy)
        let interpreter = NativeKeyboardInterpreterEvidence.applying(textPolicy, to: observation.interpreter)
        let qualified = permitsGeckoInsertion(kind, window: state.window ?? window, interpreter: interpreter)
        if geckoInsertion != nil && !qualified {
            return .init(interpreter: .init(kind: .observedWebOwnershipUnknown, area: interpreter.area))
        }
        return .init(interpreter: interpreter, permitsQualifiedLocalInsertion: qualified)
    }

    func permitsGeckoInsertion(
        _ kind: NativeKeyboardIntent.Kind, window: AXUIElement,
        interpreter: NativeKeyboardInterpreterEvidence.Observation<AXUIElement>
    ) -> Bool {
        guard kind == .insert, originalAddressedElement, receiverRelation == nil, cellEditor == nil,
            keyboardInsertion?.isPlainTextControl == true, let element, let geckoInsertion,
            interpreter.kind == .localWeb, interpreter.complete, let document = interpreter.area
        else { return false }
        return geckoInsertion.pid == application.processIdentifier
            && CFEqual(geckoInsertion.element, element) && CFEqual(geckoInsertion.document, document)
            && CFEqual(geckoInsertion.window, window) && CFEqual(self.window, window)
    }

    static func needsGeckoInsertion(selection: NativeTextSelection?, retained: NativeTextSelection?) -> Bool {
        if let retained, retained != selection { return true }
        // A qualified provider's SelectedText can handle a proved empty
        // buffer or a collapsed selection at the very start. Every other
        // position belongs to the original text receiver's keyboard path.
        guard let selection else { return true }
        return !selection.contiguous || selection.location != 0 || selection.length != 0
    }

    func textTransportSupport(
        kind: NativeKeyboardIntent.Kind, text: String?, state: State,
        textPolicy: NativeKeyboardInterpreterEvidence.Policy
    ) throws -> (supported: Bool, webRefusal: String?) {
        let proposal = try textProposal(kind: kind, text: text, state: state, textPolicy: textPolicy)
        if let proposal, proposal.unavailable || proposal.processTextUnavailable {
            return (false, proposal.refusalReason)
        }
        // Focus preparation can reveal a different editing capability or event
        // endpoint. Do not probe an unrelated current receiver before that work.
        if kind == .focus || state.receiver(element) != "yes" { return (true, nil) }
        let supported = try NativeReadiness.probeRoute(check: RequestDiagnostics.checkCancellation) {
            try BackgroundEvents.requireKeyboardRoute()
            _ = try BackgroundEvents.keyboardRecipient(
                NativeWindowUnit(application: application, window: state.window ?? window).mapping())
            return true
        }
        return (supported, nil)
    }

    /// Fresh positive mechanism evidence, not a record of an attempted input.
    func backgroundTextTransportUnavailable(
        _ request: NativeKeyboardIntent, textPolicy: NativeKeyboardInterpreterEvidence.Policy
    ) throws -> Bool {
        if request.kind == .replace, try requiresCellEditorActivation(), let element,
            try NativeCellEditorHandoff.activationNeedsForeground(
                element: element, application: application, window: window)
        {
            return true
        }
        guard request.kind == .paste
            || windowCommandScope && request.kind == .key && request.usesWindowCommand()
        else { return false }
        return try commandNeedsForeground(kind: request.kind, key: request.key, state: sample())
    }

    private func commandNeedsForeground(kind: NativeKeyboardIntent.Kind, key: String?, state: State) throws -> Bool {
        guard !state.foreground else { return false }
        let selectedWindow = state.window ?? window
        let pid = application.processIdentifier
        if kind == .paste {
            return try NativeKeyboardCommandContext.clipboardNeedsForeground(selectedWindow, pid: pid)
        }
        guard windowCommandScope, kind == .key, NativeKeyboardIntent.usesWindowCommand(kind, key: key) else {
            return false
        }
        let mapping = try NativeWindowUnit(application: application, window: selectedWindow).mapping()
        // An unrelated current focus cannot disprove the selected panel's
        // receiver. Use it only when it belongs to this exact window.
        if state.rawFocusOwner.map({ CFEqual($0, selectedWindow) }) == true, state.rawFocus != nil {
            do {
                if try NativeKeyboardCommandContext.isViewService(BackgroundEvents.keyboardRecipient(mapping)) {
                    return true
                }
            } catch let error as DriverError where error.code == .inputUnavailable {
                try RequestDiagnostics.checkCancellation()
            }
        }
        return try NativeKeyboardCommandContext.coldService(window: selectedWindow, mapping: mapping)
    }

    func requiresCellEditorActivation() throws -> Bool {
        guard cellEditorState != nil, cellEditor == nil, let element else { return false }
        return try NativeCellEditorHandoff.requiresActivation(
            element: element, window: window, pid: application.processIdentifier)
    }

    func selectedDelivery(
        _ request: NativeKeyboardIntent, textPolicy: NativeKeyboardInterpreterEvidence.Policy, state: State? = nil
    ) throws -> NativeKeyboardDelivery {
        try selectedDelivery(request, textPolicy: textPolicy, state: state, onSelection: nil)
    }

    func selectedDelivery(
        _ request: NativeKeyboardIntent, textPolicy: NativeKeyboardInterpreterEvidence.Policy, state: State? = nil,
        onSelection: ((NativeKeyboardBoundaryRouteFacts) -> Void)?
    ) throws -> NativeKeyboardDelivery {
        let state = try state ?? sample()
        let proposal = try textProposal(kind: request.kind, text: request.value, state: state, textPolicy: textPolicy)
        if let proposal {
            if proposal.unavailable {
                throw DriverError(
                    "observed Web keyboard ownership is unavailable; no requested text was sent",
                    code: .inputUnavailable)
            }
            if proposal.processTextUnavailable {
                throw NativeRouteUnavailable(description:
                    "local-Web process text isolation is unavailable; use explicit Computer input for the current desktop recipient")
            }
        }

        let mapping = try NativeWindowUnit(application: application, window: state.window ?? window).mapping()
        var recipient = try BackgroundEvents.keyboardRecipient(mapping)
        if proposal?.permitsQualifiedLocalInsertion == true, let geckoInsertion {
            guard recipient.pid == geckoInsertion.pid, recipient.presenter == geckoInsertion.pid,
                recipient.window == mapping.id, recipient.remoteRoute == nil, recipient.remoteElement == nil
            else { throw NativeRouteUnavailable(description: "qualified local text receiver has a different event endpoint") }
            recipient.expectedLocalFocus = geckoInsertion.element
        }
        let recipientRoute: String
        switch recipient.remoteRoute {
        case .some(.hostedWeb(_, _)): recipientRoute = "hostedWeb"
        case .some(.ordinaryApplication(_)): recipientRoute = "ordinaryApplication"
        case .some(.viewService(_)): recipientRoute = "service"
        case .none: recipientRoute = recipient.remoteElement == nil ? "local" : "service"
        }
        diagnose(
            [
                "kind": "keyboard_delivery_plan", "delivery": "process",
                "interpreter": proposal?.interpreter.kind.rawValue ?? "textless", "recipientRoute": recipientRoute,
                "presenterPID": recipient.presenter, "recipientPID": recipient.pid,
                "window": recipient.window as Any? ?? NSNull(),
                "qualifiedLocalInsertion": proposal?.permitsQualifiedLocalInsertion == true
            ] as [String: Any])
        let local = recipient.pid == application.processIdentifier && recipient.presenter == application.processIdentifier
            && recipient.remoteElement == nil && recipient.remoteRoute == nil
        let sameSurface = CFEqual(state.window ?? window, window)
        if let onSelection, boundaryCoverageComplete {
            let localBoundaryEvidence = try proposal.map { $0.interpreter.kind == .noObservedWeb }
                ?? (local && sameSurface && ordinaryKeyBoundaryEvidence(request, state: state, textPolicy: textPolicy))
            onSelection(.init(
                localProcess: local, localBoundaryEvidence: localBoundaryEvidence,
                sameKeyboardSurface: sameSurface, nativeID: recipient.window))
        }
        return .process(recipient)
    }

    func ordinaryKeyBoundaryEvidence(
        _ request: NativeKeyboardIntent, state: State? = nil, textPolicy: NativeKeyboardInterpreterEvidence.Policy
    ) throws -> Bool {
        guard request.kind == .key, windowCommandScope, element == nil, receiverRelation == nil,
            boundaryCoverageComplete, textPolicy == .existing
        else { return false }
        let state = try state ?? sample()
        guard state.localReceiverConfirmed, let focus = state.focus,
            state.window.map({ CFEqual($0, window) }) == true
        else { return false }
        let ancestry = try NativeKeyboardDeliveryProposal.read(
            element: focus, window: window, pid: application.processIdentifier).interpreter
        return NativeKeyboardBoundaryPolicy.ordinaryKeyEvidence(
            confirmedLocalFocus: state.localReceiverConfirmed, ancestry: ancestry)
    }

    func readiness(
        kind: NativeKeyboardIntent.Kind, key: String? = nil, text: String? = nil,
        replacement: NativeReplacementUnit.Selection? = nil, prepared: Bool = false, state: State? = nil,
        includeDelivery: Bool = true, textPolicy: NativeKeyboardInterpreterEvidence.Policy = .existing,
        use: ReadinessUse = .admission,
        replacementSnapshot: (AXUIElement, NativeReplacementUnit.Selection) -> NativeReplacementUnit.Snapshot? = {
            NativeReplacementUnit(element: $0).snapshot($1)
        }, selectionKeyReady: Bool? = nil, compositeKeyReady: Bool? = nil
    ) throws -> NativeReadiness {
        let state = try state ?? sample()
        let exact = keyboardInsertion != nil || replacement?.keyboardEntry != nil
            ? exactTextEntryReceiver(state) : state.receiver(element)
        let rangeReady: Bool
        if let selectionKeyReady { rangeReady = selectionKeyReady }
        else if use == .admission && exact != "yes" {
            rangeReady = try selectionKeyReceiver(kind: kind, key: key, state: state) != nil
        } else { rangeReady = false }
        let compositeReady: Bool
        if let compositeKeyReady { compositeReady = compositeKeyReady }
        else if use == .admission && exact != "yes" && !rangeReady {
            compositeReady = try compositeKeyReceiver(kind: kind, key: key, state: state) != nil
        } else { compositeReady = false }
        let receiver = rangeReady || compositeReady ? "yes" : exact
        let windowCommand = NativeKeyboardIntent.usesWindowCommand(
            kind, key: key, replacementShortcut: replacement?.usesWindowCommand == true)
        let needsWindow = windowCommand && !state.foreground && !prepared
        var receiverReadiness = receiver == "yes" && needsWindow ? "unknown" : receiver
        var conditions: [(String, String)] = []
        var preparationAllowed = true
        if use == .admission, let keyboardInsertion, let element {
            let selection = keyboardInsertion.selection(element)
            let confirmed = selection != nil && (insertionSelection == nil || selection == insertionSelection)
            if !confirmed {
                conditions.append(("selection", "unknown"))
                // An unresolved receiver may publish its own selection after
                // preparation. An already focused object needs that evidence
                // now, and a proved changed selection cannot be reprepared.
                preparationAllowed = receiver != "yes" && insertionSelection == nil
            }
        }
        if use == .admission, try commandNeedsForeground(kind: kind, key: key, state: state) {
            conditions.append(("foreground", "unknown"))
        }
        // A preparation receipt records this operation's native window setup,
        // not application consumption. Live receiver checks still apply at
        // every dispatch; it never authorizes activation or replay afterwards.
        if kind == .replace {
            if use == .admission {
                let selection = element.flatMap { node in replacement.flatMap { replacementSnapshot(node, $0) } }
                conditions.append(("selection", selection == nil ? "unknown" : "yes"))
                if replacement?.keyboardEntry != nil, selection == nil, receiver == "yes" {
                    preparationAllowed = false
                }
            }
            if try requiresCellEditorActivation(), let element,
                try NativeCellEditorHandoff.activationNeedsForeground(
                    element: element, application: application, window: window)
            {
                conditions.append(("foreground", "unknown"))
            }
        }
        if includeDelivery,
            let proposal = try textProposal(kind: kind, text: text, state: state, textPolicy: textPolicy)
        {
            if proposal.unavailable || proposal.processTextUnavailable {
                if receiverReadiness == "yes" { receiverReadiness = "unknown" }
                preparationAllowed = false
            }
        }
        return NativeReadiness([("receiver", receiverReadiness)] + conditions, preparationAllowed: preparationAllowed)
    }

    func exactTextEntryReceiver(_ state: State) -> String {
        guard let element, state.windowRoute == "yes", state.scopeRoute == "yes",
            let keyboardWindow = state.window, CFEqual(state.surface ?? keyboardWindow, window)
        else { return "unknown" }
        if let reported = state.rawFocus, !CFEqual(reported, element) { return "no" }
        if state.rawLocalFocus == false || state.rawFocusOwner.map({ CFEqual($0, keyboardWindow) }) == false {
            return "no"
        }
        // The normal exact-target receiver policy includes the independent
        // local proof when the application focus report is explicitly absent.
        return state.receiver(element)
    }

    private func selectionKeyReceiver(
        kind: NativeKeyboardIntent.Kind, key: String?, state: State
    ) throws -> NativeSelectionKeyReceiver? {
        guard let element, NativeSelectionKeyReceiver.accepts(kind, key: key) else { return nil }
        return try NativeSelectionKeyReceiver.capture(
            original: element, state: state, window: window, pid: application.processIdentifier)
    }

    private func compositeKeyReceiver(
        kind: NativeKeyboardIntent.Kind, key: String?, state: State
    ) throws -> NativeCompositeTextKeyReceiver? {
        guard NativeCompositeTextKeyReceiver.accepts(kind, key: key),
            originalAddressedElement, receiverRelation == nil, let element else { return nil }
        return try NativeCompositeTextKeyReceiver.capture(
            field: element, state: state, window: window, pid: application.processIdentifier)
    }

    /// Selection is sampled for admission; the replacement owner alone proves
    /// its full selection before content. Per-fragment receiver validation must
    /// not resample a selection that normal editing/autocompletion can change.
    func receiverReadiness(
        _ request: NativeKeyboardIntent, replacement: NativeReplacementUnit.Selection?, prepared: Bool, state: State,
        replacementSnapshot: (AXUIElement, NativeReplacementUnit.Selection) -> NativeReplacementUnit.Snapshot? = {
            NativeReplacementUnit(element: $0).snapshot($1)
        }, selectionKeyReady: Bool? = nil, compositeKeyReady: Bool? = nil
    ) throws -> NativeReadiness {
        try readiness(
            kind: request.kind, key: request.key, text: request.value, replacement: replacement, prepared: prepared,
            state: state, includeDelivery: false, use: .receiver, replacementSnapshot: replacementSnapshot,
            selectionKeyReady: selectionKeyReady, compositeKeyReady: compositeKeyReady)
    }

    func prepare(
        _ request: NativeKeyboardIntent, replacement: NativeReplacementUnit.Selection? = nil, foreground: Bool,
        textPolicy: NativeKeyboardInterpreterEvidence.Policy, check boundary: @escaping @MainActor () throws -> Void,
        onPreparation: @escaping @MainActor (String) -> Void, onCleanupUnknown: @escaping @MainActor () -> Void
    ) async throws {
        let ordinaryWindowKey = request.kind == .key && windowCommandScope && element == nil && receiverRelation == nil
        if ordinaryWindowKey {
            // Read-only receiver discovery precedes the check below. No
            // preparation or input intervenes between these initial queries.
            try RequestDiagnostics.checkCancellation()
        } else {
            try boundary()
        }
        let activateCell = try request.kind == .replace && requiresCellEditorActivation()
        let originalKeyboardWindow = try WindowGeometry.keyboardWindow(window, pid: application.processIdentifier)
        // Window input preserves an already confirmed attached text-root
        // receiver. Preparing the parent document would end that editing UI.
        // An addressed Element still prepares its own requested receiver.
        let initial = try (element == nil || element.map { CFEqual($0, window) } == true) ? sample() : nil
        let keyboardWindow = initial?.window ?? originalKeyboardWindow
        let attached = !CFEqual(keyboardWindow, originalKeyboardWindow)
        let continuity =
            attached ? initial.flatMap { NativeReceiverContinuity(element: element, window: window, state: $0) } : nil
        let check: @MainActor () throws -> Void = {
            try boundary()
            if attached {
                let current = try NativeReceiverSnapshot.read(
                    application: application, window: window, receiverRelation: receiverRelation)
                guard current.window.map({ CFEqual($0, keyboardWindow) }) == true else {
                    throw DriverError("the attached keyboard receiver changed during preparation", code: .staleTarget)
                }
                try continuity?.validate(current)
            } else {
                guard
                    try CFEqual(
                        WindowGeometry.keyboardWindow(window, pid: application.processIdentifier), keyboardWindow)
                else {
                    throw DriverError(
                        "the selected surface changed its keyboard window during preparation", code: .staleTarget)
                }
            }
        }
        try check()
        try checkEnabled()
        let start = try initial ?? sample()
        let requiresForeground = try (request.kind == .paste || !foreground)
            && commandNeedsForeground(kind: request.kind, key: request.key, state: start)
        if !foreground && requiresForeground {
            throw NativePreparationUnavailable(
                description: "the selected command requires the original foreground window; no requested input was sent")
        }
        let proposal = try textProposal(
            kind: request.kind, text: request.value, state: start, textPolicy: textPolicy)
        if proposal?.unavailable == true {
            throw NativePreparationUnavailable(
                description: "observed Web keyboard ownership is unavailable; no requested input was sent")
        }
        if proposal?.processTextUnavailable == true {
            throw NativeRouteUnavailable(description:
                "local-Web process text isolation is unavailable; use explicit Computer input for the current desktop recipient")
        }
        if try readiness(
            kind: request.kind, key: request.key, text: request.value, replacement: replacement,
            state: ordinaryWindowKey ? start : nil, textPolicy: textPolicy
        ).ready {
            return
        }
        let mapping = try NativeWindowUnit(application: application, window: keyboardWindow).mapping()
        if foreground {
            // Admission (host opt-in + prior background failure) is checked by
            // the operation endpoint. This helper executes only that choice.
            if request.kind == .paste && requiresForeground {
                NativeForegroundResponsibility.current?.requireForegroundForCandidate(keyWindow: start.window ?? window)
            }
            let assist = ForegroundAssist()
            try await assist.prepare(
                background, window: mapping.id, onAction: { onPreparation("foreground window activation") },
                onCleanupUnknown: onCleanupUnknown)
        } else {
            do {
                try await NativeFocusPipeline.prepare(
                    background, window: mapping, nativeWindow: keyboardWindow, onPreparation: onPreparation,
                    onCleanupUnknown: onCleanupUnknown, beforeAction: check)
            } catch let error as DriverError where error.code == .inputUnavailable {
                // An unavailable boundary, disabled control,
                // cancellation or expired operation is not fallback permission.
                try check()
                try checkEnabled()
                throw NativePreparationUnavailable(description: error.description)
            }
        }
        try check()
        let receiver = NativeReceiverPreparation(
            application: application, window: window, element: element, receiverRelation: receiverRelation,
            cellEditor: cellEditor)
        try await receiver.prepare(
            foreground: false,
            check: {
                try check()
                try checkEnabled()
            },
            ready: {
                if activateCell {
                    return try NativeReceiverSnapshot.read(
                        application: application, window: window, receiverRelation: receiverRelation
                    ).receiver(element) == "yes"
                }
                return try readiness(
                    kind: request.kind, key: request.key, text: request.value, replacement: replacement, prepared: true,
                    textPolicy: textPolicy
                ).ready
            }, onPreparation: onPreparation)
        if activateCell, let element, let cellEditorState {
            try await NativeCellEditorPreparationPipeline.run(
                element: element, application: application, window: window, state: cellEditorState,
                foreground: foreground, check: check, onPreparation: onPreparation, onCleanupUnknown: onCleanupUnknown)
        }
    }

    func deliver(
        _ request: NativeKeyboardIntent, delivery: NativeKeyboardDelivery,
        replacement: NativeReplacementUnit.Selection? = nil, prepared: Bool = false,
        check: @escaping @MainActor () throws -> Void,
        onDispatch: @escaping @MainActor () throws -> Void, onPosted: @escaping @MainActor () -> Void,
        onPreparation: @escaping @MainActor (String) -> Void,
        onClipboardRestore: (PasteboardTransaction.RestoreResult) -> Void,
        onCleanupUnknown: @escaping @MainActor () -> Void,
        allowsAttachedEditorHandoff: Bool = false,
        checkSavePanelPath: (@MainActor (NativeSavePanelPathContinuation.Proof) throws -> Void)? = nil
    ) async throws -> Bool {
        try check()
        try checkEnabled()
        try delivery.requireAdmission()
        let initial = try sample()
        let rowSelection = try initial.receiver(element) == "yes"
            ? nil : selectionKeyReceiver(kind: request.kind, key: request.key, state: initial)
        let recipient = delivery.processRecipient
        let compositeReceiver = try initial.receiver(element) != "yes" && rowSelection == nil
            && recipient?.pid == application.processIdentifier && recipient?.presenter == application.processIdentifier
            && recipient?.remoteElement == nil && recipient?.remoteRoute == nil
            ? compositeKeyReceiver(kind: request.kind, key: request.key, state: initial) : nil
        let use: ReadinessUse = keyboardInsertion != nil || replacement?.keyboardEntry != nil ? .receiver : .admission
        guard
            try readiness(
                kind: request.kind, key: request.key, text: request.value, replacement: replacement, prepared: prepared,
                state: initial, includeDelivery: false, use: use, selectionKeyReady: rowSelection != nil,
                compositeKeyReady: compositeReceiver != nil
            ).ready
        else { throw DriverError("the selected keyboard unit is not ready before dispatch", code: .inputUnavailable) }
        let keyboardWindow = initial.window ?? window
        let cellEditor =
            try request.kind == .insert
            ? NativeCellEditorHandoff.capture(
                element: element, window: keyboardWindow, focus: initial.focus, pid: application.processIdentifier)
            : nil
        // Explicit cell input shares the same retained owner only when capture
        // proved that exact element was the original focused cell.
        let continuity = NativeReceiverContinuity(
            element: cellEditor == nil ? element : nil, window: window, state: initial)
        let target = background
        let process = target.capturedProcessIdentity
        let clipboardKeyWindow = NativeForegroundResponsibility.current?.requiredKeyWindow
        let serviceCommand = windowCommandScope && request.kind == .key && request.usesWindowCommand()
            && NativeKeyboardCommandContext.isViewService(recipient)
        func validateState(_ current: inout State) throws {
            guard current.window.map({ CFEqual($0, keyboardWindow) }) == true else {
                throw DriverError(
                    "the surface's keyboard window changed; remaining input was not sent", code: .staleTarget)
            }
            if request.kind == .paste {
                try NativeKeyboardCommandContext.validateClipboard(
                    current, window: keyboardWindow, requiredKeyWindow: clipboardKeyWindow,
                    needsForeground: { try commandNeedsForeground(kind: .paste, key: nil, state: current) })
            } else if serviceCommand {
                try NativeKeyboardCommandContext.requireForeground(current, window: keyboardWindow, exactKey: false)
            }
            let ownedEditor = try cellEditor?.prove(current)
            if ownedEditor != nil, let cellEditor { cellEditorState?.retain(cellEditor) }
            // This copy belongs only to this final validation. The explicit
            // proof, not an observation hint, authorizes the retained owner.
            if let ownedEditor { current.relatedEditor = ownedEditor.owner }
            try continuity?.validate(current, ownedEditor: ownedEditor)
            if let rowSelection,
                try !rowSelection.matches(selectionKeyReceiver(kind: request.kind, key: request.key, state: current))
            {
                throw DriverError("the original selected row range changed before its key command", code: .staleTarget)
            }
            if let compositeReceiver,
                try !compositeReceiver.matches(compositeKeyReceiver(kind: request.kind, key: request.key, state: current))
            {
                throw DriverError("the original compound text receiver changed before its key", code: .staleTarget)
            }
            guard try self.receiverReadiness(
                request, replacement: replacement, prepared: prepared, state: current,
                selectionKeyReady: rowSelection != nil, compositeKeyReady: compositeReceiver != nil).ready
            else {
                throw DriverError(
                    "keyboard unit readiness changed; remaining key events were not sent", code: .inputUnavailable)
            }
        }
        func validateReceiver() async throws {
            try check()
            try self.checkEnabled()
            var current = try self.sample()
            try validateState(&current)
        }
        func handoffBeforeFirstKeyDown() async throws {
            // Preparation can activate an application after its native step
            // returned. The operation still owns that excursion. Return before
            // the first requested down, with the already fixed local receiver;
            // neither a new field nor a replacement selection is chosen here.
            guard initial.localReceiverConfirmed, let originalFocus = initial.focus,
                let recipient, recipient.pid == application.processIdentifier,
                recipient.presenter == application.processIdentifier, recipient.remoteElement == nil,
                recipient.remoteRoute == nil, let process,
                let responsibility = NativeForegroundResponsibility.current,
                let original = responsibility.original, original.pid != process.pid
            else { return }
            let ownership = responsibility.state(target: process)
            guard responsibility.hasForegroundObligation, !responsibility.retainsForegroundForInput,
                !ownership.externalActivation,
                ownership.foregroundPID == process.pid, responsibility.permitsForegroundPreparation()
            else { return }
            func checkOriginal() throws {
                try check()
                try self.checkEnabled()
                guard NativeProcessIdentity.read(application.processIdentifier) == process else {
                    throw DriverError("keyboard process changed before foreground return", code: .staleTarget)
                }
                var fresh = try self.sample()
                try validateState(&fresh)
                let mapping = try NativeWindowUnit(application: application, window: keyboardWindow).mapping()
                guard fresh.localReceiverConfirmed,
                    fresh.focus.map({ CFEqual($0, originalFocus) }) == true,
                    mapping.pid == recipient.pid, mapping.id == recipient.window
                else {
                    throw DriverError("keyboard receiver changed before foreground return", code: .staleTarget)
                }
                try recipient.validateFocus()
            }
            try checkOriginal()
            diagnose(["kind": "keyboard_foreground_handoff", "phase": "beforeFirstKeyDown",
                      "presenterPID": application.processIdentifier, "recipientPID": recipient.pid,
                      "window": recipient.window as Any? ?? NSNull(), "requestedKeyDownAttempted": false])
            try await NativeInputActivity.$stage.withValue(.preparation) {
                try await NativeForegroundRestorationScope.perform(
                    target: target, onPreparation: onPreparation, onCleanupUnknown: onCleanupUnknown
                ) { try checkOriginal() }
            }
            // Fresh first-responder facts survive expected AX deactivation.
            // Do not send another activity notification or renew a cache lease:
            // either would undo the return and can erase a previous selection.
            try checkOriginal()
            let after = responsibility.state(target: process)
            diagnose(["kind": "keyboard_foreground_handoff", "phase": "afterReturn",
                      "recipientPID": recipient.pid, "window": recipient.window as Any? ?? NSNull(),
                      "foregroundPID": after.foregroundPID as Any? ?? NSNull(),
                      "externalActivation": after.externalActivation, "requestedKeyDownAttempted": false,
                      "replayedKeyEvents": 0])
        }
        switch request.kind {
        case .focus: throw DriverError("focus is preparation, not keyboard delivery", code: .invalidRequest)
        case .key:
            try await Input.preparedKey(
                request.value!, delivery: delivery, validate: validateReceiver, onDispatch: onDispatch,
                onPosted: onPosted, onCleanupUnknown: onCleanupUnknown,
                beforeFirstKeyDown: handoffBeforeFirstKeyDown)
        case .insert:
            var started = false
            var continuationState: State?
            func validateInsertionSelection() throws {
                guard let keyboardInsertion, let element,
                    NativeKeyboardTextEntry.read(element) == keyboardInsertion,
                    let selection = keyboardInsertion.selection(element),
                    insertionSelection == nil || selection == insertionSelection
                else {
                    throw DriverError("original keyboard selection is unconfirmed; no text sent", code: .inputUnavailable)
                }
            }
            func validateInsertion() async throws {
                if !started, let geckoInsertion, let element {
                    guard try NativeGeckoTextProvider.prove(
                        element: element, presenterPID: application.processIdentifier, originalWindow: { keyboardWindow }
                    ) == geckoInsertion else {
                        throw NativeRouteUnavailable(description: "the original process text provider is unconfirmed")
                    }
                }
                if var state = continuationState {
                    try check()
                    try self.checkEnabled()
                    try validateState(&state)
                } else {
                    try await validateReceiver()
                }
                if !started, keyboardInsertion != nil {
                    try validateInsertionSelection()
                    // The qualified recipient checks the exact original
                    // focus after this range read, before every scalar down.
                    if geckoInsertion == nil { try await validateReceiver() }
                }
            }
            let resolveSavePanelPath: (@MainActor (String) throws -> NativeSavePanelPathContinuation.Proof?)?
            if let checkSavePanelPath, cellEditor == nil, receiverRelation == nil,
                let path = try NativeSavePanelPathContinuation.capture(
                    window: keyboardWindow, element: element, text: request.value!, state: initial, delivery: delivery)
            {
                resolveSavePanelPath = { posted in
                    guard !posted.isEmpty else { return nil }
                    let state = try self.sample()
                    continuationState = state
                    if let proof = try path.resolve(postedPrefix: posted, state: state) {
                        try checkSavePanelPath(proof)
                        return proof
                    }
                    guard !path.transitioned else {
                        throw DriverError("the Go To path field changed before the remaining input", code: .staleTarget)
                    }
                    return nil
                }
            } else { resolveSavePanelPath = nil }
            let resolveAttachedEditor: (@MainActor () throws -> NativeAppEditorHandoff.Proof?)?
            if allowsAttachedEditorHandoff, element == nil, receiverRelation == nil,
                let handoff = try NativeAppEditorHandoff.capture(
                    application: application, window: window, state: initial, delivery: delivery)
            {
                resolveAttachedEditor = {
                    let state = try self.sample()
                    continuationState = state
                    if let proof = try handoff.resolve(state) {
                        try check()
                        return proof
                    }
                    guard !handoff.transitioned else {
                        throw DriverError("the attached text receiver changed; remaining input was not sent", code: .staleTarget)
                    }
                    return nil
                }
            } else { resolveAttachedEditor = nil }
            try await Input.preparedText(
                request.value!, delivery: delivery, applicationPID: application.processIdentifier,
                validate: validateInsertion, onDispatch: onDispatch, onPosted: {
                    started = true
                    onPosted()
                },
                onCleanupUnknown: onCleanupUnknown, beforeFirstKeyDown: handoffBeforeFirstKeyDown,
                savePanelPath: resolveSavePanelPath, attachedEditor: resolveAttachedEditor)
        case .replace:
            guard let element, let replacement else {
                throw DriverError("replacement requires an addressed editing/selection contract", code: .invalidRequest)
            }
            let windowReceiver: NativeReplacementWindowReceiver?
            if originalAddressedElement, replacement == .range, self.cellEditor == nil, receiverRelation == nil,
                initial.foreground, initial.focus.map({ CFEqual($0, element) }) == true,
                let process, let recipient, recipient.pid == process.pid, recipient.presenter == process.pid,
                recipient.remoteElement == nil, recipient.remoteRoute == nil, let windowID = recipient.window
            {
                let desktop = NativeForegroundResponsibility.desktopSnapshot()
                func desktopUnchanged() -> Bool {
                    let current = NativeForegroundResponsibility.desktopSnapshot()
                    return desktop.foregroundPID == process.pid && current.foregroundPID == process.pid
                        && current.activity == desktop.activity
                }
                func windowOnly() throws -> Bool {
                    try check()
                    try self.checkEnabled()
                    guard NativeProcessIdentity.read(process.pid) == process else {
                        throw DriverError("replacement receiver process changed", code: .staleTarget)
                    }
                    // A Window-only sample cannot prove the usual concrete
                    // keyboard endpoint. Check the same original local
                    // transport separately without preparing a new endpoint.
                    let state = try NativeReceiverSnapshot.read(application: application, window: window)
                    guard NativeReplacementWindowReceiver.isWindowOnly(state, original: element, window: keyboardWindow)
                    else { return false }
                    let mapping = try NativeWindowUnit(application: application, window: keyboardWindow).mapping()
                    guard mapping.pid == process.pid, mapping.id == windowID else { return false }
                    try RequestDiagnostics.checkCancellation()
                    return desktopUnchanged()
                }
                windowReceiver = .init(
                    windowOnly: windowOnly,
                    canSetFocus: { AXAccess.writable(element, kAXFocusedAttribute).value == true },
                    focusOriginal: {
                        onPreparation("native replacement receiver recovery")
                        let status = NativeInputActivity.$stage.withValue(.preparation) {
                            AXMessaging.setAttributeValue(element, kAXFocusedAttribute as CFString, kCFBooleanTrue)
                        }
                        guard status == .success else {
                            throw DriverError(
                                "replacement receiver recovery returned AX error \(status.rawValue)",
                                code: .inputUnavailable)
                        }
                    }, desktopUnchanged: desktopUnchanged)
            } else { windowReceiver = nil }
            return try await NativeReplacementUnit(element: element).deliver(
                text: request.value!, selection: replacement, delivery: delivery,
                applicationPID: application.processIdentifier,
                check: {
                    try check()
                    try self.checkEnabled()
                }, validateReceiver: validateReceiver, onDispatch: onDispatch, onPosted: onPosted,
                onCleanupUnknown: onCleanupUnknown, windowReceiver: windowReceiver)
        case .paste:
            let transaction = try PasteboardTransaction()
            var sent = false
            // Cleanup stays inside this selected unit and its operation lease,
            // on success, cancellation and partial/unknown input alike.
            do {
                try await validateReceiver()
                onPreparation("temporary clipboard payload")
                try transaction.install(request.value!)
                try await Input.preparedKey(
                    "super+v", delivery: delivery,
                    validate: {
                        try await validateReceiver()
                        try transaction.validateOwnership()
                    }, onDispatch: onDispatch,
                    onPosted: {
                        sent = true
                        onPosted()
                    }, onCleanupUnknown: onCleanupUnknown, beforeFirstKeyDown: handoffBeforeFirstKeyDown)
            } catch {
                onClipboardRestore(await transaction.finish(afterInput: sent))
                throw error
            }
            onClipboardRestore(await transaction.finish(afterInput: sent))
        }
        return false
    }
}
