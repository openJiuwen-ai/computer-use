// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices

/// One fixed receiver and one typed keyboard delivery. This unit never
/// searches for an editor, clicks content, or chooses foreground fallback.
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
    /// Only the qualified dispatch copy compares this immutable original ID.
    var dispatchExpectedNativeID: CGWindowID? = nil

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
        let ownedEditor = try cellEditor.map { proof in
            try proof.association(context).map { owned in element.map { CFEqual($0, owned.editor) } == true } == true
        } ?? true
        let deliveryReady = try NativeReadiness.probeRoute(check: RequestDiagnostics.checkCancellation) {
            let mapping = try NativeWindowUnit(application: application, window: context.window ?? window).mapping()
            if let expected = dispatchExpectedNativeID, mapping.id != expected {
                throw DriverError("original keyboard native window changed", code: .staleTarget)
            }
            return BackgroundEvents.keyboardPreparationIsCurrent(background, window: mapping)
        }
        return State(
            windowRoute: context.windowRoute == "yes" && !deliveryReady ? "no" : context.windowRoute,
            // Incomplete current association grants no Element receiver. Keep
            // window preparation available; only a later fresh proof can admit
            // focus on this editor or requested input after that preparation.
            focus: ownedEditor ? context.focus : nil, ancestors: ownedEditor ? context.ancestors : [],
            foreground: context.foreground,
            relatedEditor: ownedEditor ? context.relatedEditor : nil, window: context.window, surface: context.surface,
            scopeRoute: context.scopeRoute, reportedFocus: context.reportedFocus,
            focusReportAbsent: ownedEditor && context.focusReportAbsent, focusedWindowRead: context.focusedWindowRead,
            focusedWindowStatus: context.focusedWindowStatus, rawFocus: context.rawFocus,
            rawFocusOwner: context.rawFocusOwner, rawLocalFocus: context.rawLocalFocus)
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
        return .init(interpreter: NativeKeyboardInterpreterEvidence.applying(textPolicy, to: observation.interpreter))
    }

    /// A same-App but other-window focus is not an already-foreground scope.
    private func requiresForegroundSelection(_ proposal: NativeKeyboardDeliveryProposal, state: State) -> Bool {
        var windowState = NativeKeyboardTransportPolicy.Window.unavailable
        let expected = state.window ?? window
        let expectedID = BackgroundEvents.nativeWindowID(expected)
        if let read = state.focusedWindowRead, case .value(let raw) = read, CFGetTypeID(raw) == AXUIElementGetTypeID() {
            let focused = unsafeDowncast(raw, to: AXUIElement.self)
            if CFEqual(focused, expected) {
                windowState = .target
            } else if let original = expectedID, let actual = BackgroundEvents.nativeWindowID(focused) {
                windowState = original == actual ? .target : .other
            }
        }
        if windowState == .target, let id = expectedID, let row = WindowServerInfo.window(id),
            (row[kCGWindowIsOnscreen as String] as? NSNumber)?.boolValue == false
        {
            windowState = .hidden
        }
        return NativeKeyboardTransportPolicy.select(
            usesText: true, interpreter: proposal.interpreter.kind, applicationForeground: background.isForeground,
            window: windowState) == .foregroundPreparation
    }

    func textTransportSupported(
        kind: NativeKeyboardIntent.Kind, text: String?, state: State,
        textPolicy: NativeKeyboardInterpreterEvidence.Policy
    ) throws -> Bool {
        let proposal = try textProposal(kind: kind, text: text, state: state, textPolicy: textPolicy)
        if proposal?.unavailable == true { return false }
        // Focus preparation can reveal a different editing capability or event
        // endpoint. Do not probe an unrelated current receiver before that work.
        // Scoped HID owns its separate admission proof, independently of a PID.
        if kind == .focus || state.receiver(element) != "yes" || proposal?.needsForeground == true { return true }
        return try NativeReadiness.probeRoute(check: RequestDiagnostics.checkCancellation) {
            try BackgroundEvents.requireKeyboardRoute()
            _ = try BackgroundEvents.keyboardRecipient(
                NativeWindowUnit(application: application, window: state.window ?? window).mapping())
            return true
        }
    }

    /// Fresh positive mechanism evidence, not a record of an attempted input.
    func backgroundTextTransportUnavailable(
        _ request: NativeKeyboardIntent, textPolicy: NativeKeyboardInterpreterEvidence.Policy
    ) throws -> Bool {
        if request.kind == .replace, try requiresCellEditorActivation(), let element,
            try NativeCellEditorHandoff.activationNeedsForeground(
                element: element, application: application, window: window)
        { return true }
        let state = try sample()
        guard
            let proposal = try textProposal(
                kind: request.kind, text: request.value, state: state, textPolicy: textPolicy)
        else { return false }
        return requiresForegroundSelection(proposal, state: state)
    }

    func requiresCellEditorActivation() throws -> Bool {
        guard cellEditorState != nil, cellEditor == nil, let element else { return false }
        return try NativeCellEditorHandoff.requiresActivation(
            element: element, window: window, pid: application.processIdentifier)
    }

    /// Bounded queries shared by this captured plan within one native execute.
    /// A later public prepare/dispatch RPC recaptures under the original operation deadline.
    func confirmSelectedDelivery(_ delivery: NativeKeyboardDelivery, check: () throws -> Void) async throws {
        guard case .scopedForegroundHID(let scoped) = delivery else { return }
        do {
            try await scoped.readinessGate.confirm(
                check: RequestDiagnostics.checkCancellation,
                sample: {
                    try check()
                    return try NativeKeyboardReadinessGate.queryThenReceiver(
                        mechanism: scoped.mechanismEvidence, receiver: { scoped.receiverState(try self.sample()) }
                    ) { scoped.windowEvidence(state: $0) }
                })
        } catch NativeKeyboardReadinessGate.Failure.unavailable(let reason) {
            throw DriverError("scoped keyboard window confirmation unavailable: " + reason, code: .inputUnavailable)
        } catch NativeKeyboardReadinessGate.Failure.conflicting(let reason) {
            throw DriverError(reason, code: .staleTarget)
        }
    }

    func selectedDelivery(
        _ request: NativeKeyboardIntent, textPolicy: NativeKeyboardInterpreterEvidence.Policy, state: State? = nil
    ) throws -> NativeKeyboardDelivery {
        try selectedDelivery(request, textPolicy: textPolicy, state: state, onSelection: { _ in })
    }

    func selectedDelivery(
        _ request: NativeKeyboardIntent, textPolicy: NativeKeyboardInterpreterEvidence.Policy, state: State? = nil,
        onSelection: (NativeKeyboardBoundaryRouteFacts) -> Void
    ) throws -> NativeKeyboardDelivery {
        let state = try state ?? sample()
        let proposal = try textProposal(kind: request.kind, text: request.value, state: state, textPolicy: textPolicy)
        if let proposal {
            if proposal.unavailable {
                throw DriverError(
                    "observed Web keyboard ownership is unavailable; no requested text was sent",
                    code: .inputUnavailable)
            }
            if proposal.needsForeground {
                guard background.isForeground else {
                    throw NativePreparationUnavailable(
                        description:
                            "local-Web process text isolation is unavailable in background; no requested input was sent"
                    )
                }
                let keyboardWindow = state.window ?? window
                let editor =
                    element.flatMap { CFEqual($0, window) ? nil : $0 } ?? state.relatedEditor ?? state.focus
                    ?? state.rawFocus
                guard let editor, let area = proposal.interpreter.area,
                    let process = NativeProcessIdentity.read(application.processIdentifier)
                else { throw DriverError("scoped keyboard target identity is unavailable", code: .staleTarget) }
                diagnose(
                    [
                        "kind": "keyboard_delivery_plan", "delivery": "scopedForegroundHID",
                        "interpreter": proposal.interpreter.kind.rawValue, "presenterPID": process.pid,
                        "window": BackgroundEvents.nativeWindowID(keyboardWindow) as Any? ?? NSNull()
                    ] as [String: Any])
                return .scopedForegroundHID(
                    .init(
                        application: application, window: keyboardWindow, surface: window,
                        mapping: try NativeWindowUnit(application: application, window: keyboardWindow).mapping(),
                        editor: editor, area: area, process: process))
            }
        }
        let recipient = try BackgroundEvents.keyboardRecipient(
            NativeWindowUnit(application: application, window: state.window ?? window).mapping())
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
                "window": recipient.window as Any? ?? NSNull()
            ] as [String: Any])
        onSelection(
            .init(
                localProcess: recipient.pid == application.processIdentifier
                    && recipient.presenter == application.processIdentifier && recipient.remoteElement == nil
                    && recipient.remoteRoute == nil, noObservedWeb: proposal?.interpreter.kind == .noObservedWeb,
                sameKeyboardSurface: CFEqual(state.window ?? window, window), nativeID: recipient.window))
        return .process(recipient)
    }

    func readiness(
        kind: NativeKeyboardIntent.Kind, key: String? = nil, text: String? = nil,
        replacement: NativeReplacementUnit.Selection? = nil, prepared: Bool = false, state: State? = nil,
        includeDelivery: Bool = true, textPolicy: NativeKeyboardInterpreterEvidence.Policy = .existing,
        use: ReadinessUse = .admission,
        replacementSnapshot: (AXUIElement, NativeReplacementUnit.Selection) -> NativeReplacementUnit.Snapshot? = {
            NativeReplacementUnit(element: $0).snapshot($1)
        }
    ) throws -> NativeReadiness {
        let state = try state ?? sample()
        let receiver = state.receiver(element)
        let windowCommand = NativeKeyboardIntent.usesWindowCommand(
            kind, key: key, replacementShortcut: replacement?.usesWindowCommand == true)
        let needsWindow = windowCommand && !state.foreground && !prepared
        var conditions = [("receiver", receiver == "yes" && needsWindow ? "unknown" : receiver)]
        // A preparation receipt records this operation's native window setup,
        // not application consumption. Live receiver checks still apply at
        // every dispatch; it never authorizes activation or replay afterwards.
        if kind == .replace {
            if use == .admission {
                let selection = element.flatMap { node in
                    replacement.flatMap { replacementSnapshot(node, $0) }
                }
                conditions.append(("selection", selection == nil ? "unknown" : "yes"))
            }
            if try requiresCellEditorActivation(), let element,
                try NativeCellEditorHandoff.activationNeedsForeground(
                    element: element, application: application, window: window)
            { conditions.append(("foreground", "unknown")) }
        }
        if includeDelivery,
            let proposal = try textProposal(kind: kind, text: text, state: state, textPolicy: textPolicy)
        {
            if proposal.unavailable || requiresForegroundSelection(proposal, state: state) {
                conditions.append(("foreground", "unknown"))
            }
        }
        return NativeReadiness(conditions)
    }

    /// Selection is sampled for admission; the replacement owner alone proves
    /// its full selection before content. Per-fragment receiver validation must
    /// not resample a selection that normal editing/autocompletion can change.
    func receiverReadiness(
        _ request: NativeKeyboardIntent, replacement: NativeReplacementUnit.Selection?, prepared: Bool, state: State,
        replacementSnapshot: (AXUIElement, NativeReplacementUnit.Selection) -> NativeReplacementUnit.Snapshot? = {
            NativeReplacementUnit(element: $0).snapshot($1)
        }
    ) throws -> NativeReadiness {
        try readiness(
            kind: request.kind, key: request.key, text: request.value, replacement: replacement,
            prepared: prepared, state: state, includeDelivery: false, use: .receiver,
            replacementSnapshot: replacementSnapshot)
    }

    func prepare(
        _ request: NativeKeyboardIntent, replacement: NativeReplacementUnit.Selection? = nil, foreground: Bool,
        textPolicy: NativeKeyboardInterpreterEvidence.Policy, check boundary: @escaping @MainActor () throws -> Void,
        onPreparation: @escaping @MainActor (String) -> Void, onCleanupUnknown: @escaping @MainActor () -> Void
    ) async throws {
        try boundary()
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
        try BackgroundTarget.validateReleasedInput()
        let proposal = try textProposal(
            kind: request.kind, text: request.value, state: try initial ?? sample(), textPolicy: textPolicy)
        if proposal?.unavailable == true {
            throw NativePreparationUnavailable(
                description: "observed Web keyboard ownership is unavailable; no requested input was sent")
        }
        if let proposal, requiresForegroundSelection(proposal, state: try initial ?? sample()), !foreground {
            throw NativePreparationUnavailable(
                description: """
                    local-Web background text transport is unavailable by fresh mechanism evidence; no \
                    requested input was sent
                    """)
        }
        if try readiness(
            kind: request.kind, key: request.key, text: request.value, replacement: replacement, textPolicy: textPolicy
        ).ready, proposal?.needsForeground != true {
            return
        }
        let mapping = try NativeWindowUnit(application: application, window: keyboardWindow).mapping()
        if foreground {
            // Admission (host opt-in + prior background failure) is checked by
            // the operation endpoint. This helper executes only that choice.
            let assist = ForegroundAssist()
            try await assist.prepare(
                background, window: mapping.id, onAction: { onPreparation("foreground window activation") },
                onCleanupUnknown: onCleanupUnknown)
        } else if proposal?.needsForeground != true {
            if request.usesWindowCommand(replacementShortcut: replacement?.usesWindowCommand == true)
                    && !background.isForeground,
                AXAccess.read(keyboardWindow, kAXRoleAttribute).value as? String == kAXWindowRole,
                AXAccess.read(keyboardWindow, kAXSubroleAttribute).value as? String == kAXStandardWindowSubrole,
                AXAccess.read(keyboardWindow, kAXFocusedAttribute).value as? Bool == false,
                AXAccess.writable(keyboardWindow, kAXFocusedAttribute).value == false
            {
                let root = AXUIElementCreateApplication(application.processIdentifier)
                AXUIElementSetMessagingTimeout(root, 0.1)
                if AXAccess.writable(root, kAXFocusedWindowAttribute).value == false {
                    let selection = NativeKeyboardWindowEvidence.BackgroundSelection(
                        // Command-key equivalents use the window/application
                        // responder chain even when an Element is addressed.
                        windowScope: true, windowCommand: true,
                        foreground: false, standardWindow: true, focused: false, windowFocusWritable: false,
                        appFocusWritable: false, main: AXAccess.read(keyboardWindow, kAXMainAttribute).value as? Bool,
                        mainWindowMatches: AXAccess.element(root, kAXMainWindowAttribute).map {
                            CFEqual($0, keyboardWindow)
                        },
                        focusedWindowMatches: AXAccess.element(root, kAXFocusedWindowAttribute).map {
                            CFEqual($0, keyboardWindow)
                        })
                    try check()
                    if NativeKeyboardWindowEvidence.prefersForegroundBeforeSynthetic(selection) {
                        throw NativePreparationUnavailable(
                            description: """
                                use the authorized foreground document preparation before synthetic \
                                activation; no requested input was sent
                                """)
                    }
                }
            }
            do {
                try await NativeFocusPipeline.prepare(
                    background, window: mapping, nativeWindow: keyboardWindow, onPreparation: onPreparation,
                    onCleanupUnknown: onCleanupUnknown, beforeAction: check)
            } catch let error as DriverError where error.code == .inputUnavailable {
                // An unavailable boundary, disabled control, held user input,
                // cancellation or expired operation is not fallback permission.
                try check()
                try checkEnabled()
                try BackgroundTarget.validateReleasedInput()
                throw NativePreparationUnavailable(description: error.description)
            }
        }
        try check()
        if proposal?.needsForeground == true {
            let delivery = try selectedDelivery(request, textPolicy: textPolicy)
            try await confirmSelectedDelivery(delivery, check: check)
            if case .scopedForegroundHID(let scoped) = delivery {
                try NativeScopedKeyboardPreparation.prepare(scoped, check: check, onPreparation: onPreparation)
            }
        }
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
        check: @escaping @MainActor () throws -> Void, prepareDispatch: @escaping @MainActor () throws -> Void = {},
        onDispatch: @escaping @MainActor () throws -> Void, onPosted: @escaping @MainActor () -> Void,
        onPreparation: (String) -> Void, onClipboardRestore: (PasteboardTransaction.RestoreResult) -> Void,
        onCleanupUnknown: @escaping () -> Void
    ) async throws -> Bool {
        try check()
        try checkEnabled()
        try delivery.requireAdmission()
        try BackgroundTarget.validateReleasedInput()
        var initial = try sample()
        if case .scopedForegroundHID(let scoped) = delivery { initial = scoped.receiverState(initial) }
        guard
            try readiness(
                kind: request.kind, key: request.key, text: request.value, replacement: replacement, prepared: prepared,
                state: initial, includeDelivery: false
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
        let readinessGate: NativeKeyboardReadinessGate
        if case .scopedForegroundHID(let scoped) = delivery {
            readinessGate = scoped.readinessGate
        } else {
            readinessGate = NativeKeyboardReadinessGate(waits: 0)
        }
        func validateState(_ current: inout State) throws {
            guard current.window.map({ CFEqual($0, keyboardWindow) }) == true else {
                throw DriverError(
                    "the surface's keyboard window changed; remaining input was not sent", code: .staleTarget)
            }
            let ownedEditor = try cellEditor?.prove(current)
            if ownedEditor != nil, let cellEditor { cellEditorState?.retain(cellEditor) }
            // This copy belongs only to this final validation. The explicit
            // proof, not an observation hint, authorizes the retained owner.
            if let ownedEditor { current.relatedEditor = ownedEditor.owner }
            try continuity?.validate(current, ownedEditor: ownedEditor)
            guard try self.receiverReadiness(
                request, replacement: replacement, prepared: prepared, state: current
            ).ready else {
                throw DriverError(
                    "keyboard unit readiness changed; remaining key events were not sent", code: .inputUnavailable)
            }
        }
        func validateReceiver() async throws {
            if case .scopedForegroundHID(let scoped) = delivery {
                do {
                    try await readinessGate.confirm(
                        check: RequestDiagnostics.checkCancellation,
                        sample: {
                            try check()
                            try self.checkEnabled()
                            try prepareDispatch()
                            return try NativeKeyboardReadinessGate.queryThenReceiver(
                                mechanism: scoped.mechanismEvidence,
                                receiver: { scoped.receiverState(try self.sample()) }
                            ) { current in
                                let evidence = scoped.windowEvidence(state: current)
                                if case .ready = evidence {
                                    try validateState(&current)
                                    return try scoped.finalReceiverEvidence(current)
                                }
                                return evidence
                            }
                        })
                } catch NativeKeyboardReadinessGate.Failure.unavailable(let reason) {
                    throw DriverError(
                        "scoped keyboard window confirmation unavailable: " + reason + "; remaining input was not sent",
                        code: .inputUnavailable)
                } catch NativeKeyboardReadinessGate.Failure.conflicting(let reason) {
                    throw DriverError(reason + "; remaining input was not sent", code: .staleTarget)
                }
            } else {
                try check()
                try self.checkEnabled()
                var current = try self.sample()
                try validateState(&current)
            }
        }
        switch request.kind {
        case .focus: throw DriverError("focus is preparation, not keyboard delivery", code: .invalidRequest)
        case .key:
            try await Input.preparedKey(
                request.value!, delivery: delivery, validate: validateReceiver, onDispatch: onDispatch,
                onPosted: onPosted, onCleanupUnknown: onCleanupUnknown)
        case .insert:
            try await Input.preparedText(
                request.value!, delivery: delivery, applicationPID: application.processIdentifier,
                validate: validateReceiver, onDispatch: onDispatch, onPosted: onPosted,
                onCleanupUnknown: onCleanupUnknown)
        case .replace:
            guard let element, let replacement else {
                throw DriverError("replacement requires an addressed editing/selection contract", code: .invalidRequest)
            }
            return try await NativeReplacementUnit(element: element).deliver(
                text: request.value!, selection: replacement, delivery: delivery,
                applicationPID: application.processIdentifier,
                check: {
                    try check()
                    try self.checkEnabled()
                }, validateReceiver: validateReceiver, onDispatch: onDispatch, onPosted: onPosted,
                onCleanupUnknown: onCleanupUnknown)
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
                    }, onCleanupUnknown: onCleanupUnknown)
            } catch {
                onClipboardRestore(await transaction.finish(afterInput: sent))
                throw error
            }
            onClipboardRestore(await transaction.finish(afterInput: sent))
        }
        return false
    }
}
