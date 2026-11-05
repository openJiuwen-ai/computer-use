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
        return .init(interpreter: NativeKeyboardInterpreterEvidence.applying(textPolicy, to: observation.interpreter))
    }

    func textTransportSupported(
        kind: NativeKeyboardIntent.Kind, text: String?, state: State,
        textPolicy: NativeKeyboardInterpreterEvidence.Policy
    ) throws -> Bool {
        let proposal = try textProposal(kind: kind, text: text, state: state, textPolicy: textPolicy)
        if proposal?.unavailable == true || proposal?.processTextUnavailable == true { return false }
        // Focus preparation can reveal a different editing capability or event
        // endpoint. Do not probe an unrelated current receiver before that work.
        if kind == .focus || state.receiver(element) != "yes" { return true }
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
        {
            return true
        }
        return false
    }

    func requiresCellEditorActivation() throws -> Bool {
        guard cellEditorState != nil, cellEditor == nil, let element else { return false }
        return try NativeCellEditorHandoff.requiresActivation(
            element: element, window: window, pid: application.processIdentifier)
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
            if proposal.processTextUnavailable {
                throw NativeRouteUnavailable(description:
                    "local-Web process text isolation is unavailable; use explicit Computer input for the current desktop recipient")
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
                let selection = element.flatMap { node in replacement.flatMap { replacementSnapshot(node, $0) } }
                conditions.append(("selection", selection == nil ? "unknown" : "yes"))
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
                conditions.append(("textTransport", "unknown"))
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
            kind: request.kind, key: request.key, text: request.value, replacement: replacement, prepared: prepared,
            state: state, includeDelivery: false, use: .receiver, replacementSnapshot: replacementSnapshot)
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
        let proposal = try textProposal(
            kind: request.kind, text: request.value, state: try initial ?? sample(), textPolicy: textPolicy)
        if proposal?.unavailable == true {
            throw NativePreparationUnavailable(
                description: "observed Web keyboard ownership is unavailable; no requested input was sent")
        }
        if proposal?.processTextUnavailable == true {
            throw NativeRouteUnavailable(description:
                "local-Web process text isolation is unavailable; use explicit Computer input for the current desktop recipient")
        }
        if try readiness(
            kind: request.kind, key: request.key, text: request.value, replacement: replacement, textPolicy: textPolicy
        ).ready {
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
        onCleanupUnknown: @escaping @MainActor () -> Void
    ) async throws -> Bool {
        try check()
        try checkEnabled()
        try delivery.requireAdmission()
        let initial = try sample()
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
        let target = background
        let process = target.capturedProcessIdentity
        let recipient = delivery.processRecipient
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
                request, replacement: replacement, prepared: prepared, state: current).ready
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
            try await Input.preparedText(
                request.value!, delivery: delivery, applicationPID: application.processIdentifier,
                validate: validateReceiver, onDispatch: onDispatch, onPosted: onPosted,
                onCleanupUnknown: onCleanupUnknown, beforeFirstKeyDown: handoffBeforeFirstKeyDown)
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
