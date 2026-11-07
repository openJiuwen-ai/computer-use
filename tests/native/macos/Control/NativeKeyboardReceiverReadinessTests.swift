// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices

@main enum NativeKeyboardReceiverReadinessTests {
    enum Stop: Error { case failed }

    static func facts(
        windowRoute: String = "yes", scopeRoute: String = "yes", concrete: Bool = true,
        focusedWindow: Bool = true, focusOwner: Bool = true, locallyFocused: Bool? = true,
        actualPID: Int32? = 91071, presenterPID: Int32 = 91071, nativeWindow: Bool = true
    ) -> NativeKeyboardReceiverReadiness.Facts {
        .init(
            windowRoute: windowRoute, scopeRoute: scopeRoute, concreteFocus: concrete,
            focusedWindowMatches: focusedWindow, focusOwnerMatches: focusOwner,
            locallyFocused: locallyFocused, actualPID: actualPID, presenterPID: presenterPID,
            nativeWindowKnown: nativeWindow)
    }

    static func onlyConfirmedLocalReceiverQualifies() {
        precondition(NativeKeyboardReceiverReadiness.confirmedLocal(facts()))
        let refused = [
            facts(windowRoute: "unknown"), facts(windowRoute: "no"),
            facts(scopeRoute: "unknown"), facts(scopeRoute: "no"),
            facts(concrete: false), facts(focusedWindow: false), facts(focusOwner: false),
            facts(locallyFocused: false), facts(locallyFocused: nil),
            facts(actualPID: nil), facts(actualPID: 91072), facts(nativeWindow: false),
        ]
        for value in refused {
            precondition(!NativeKeyboardReceiverReadiness.confirmedLocal(value),
                         "incomplete or foreign receiver facts cannot replace preparation evidence")
        }
    }

    @MainActor static func windowCommandsKeepTheirPreparation() throws {
        let window = AXUIElementCreateApplication(91071)
        let editor = AXUIElementCreateApplication(91072)
        let other = AXUIElementCreateApplication(91073)
        let state = NativeReceiverSnapshot(
            windowRoute: "yes", focus: editor, ancestors: [], foreground: false, window: window, surface: window,
            localReceiverConfirmed: true)
        let unit = NativeKeyboardUnit(application: .current, window: window, element: editor)
        let insertion = try unit.readiness(kind: .insert, text: "X", state: state, includeDelivery: false)
        precondition(insertion.ready)
        let command = try unit.readiness(kind: .key, key: "super+a", state: state, includeDelivery: false)
        precondition(!command.ready, "a confirmed editor does not prepare AppKit window-command dispatch")
        let paste = try unit.readiness(kind: .paste, text: "X", state: state, includeDelivery: false)
        precondition(!paste.ready)
        let preparedCommand = try unit.readiness(
            kind: .key, key: "super+a", prepared: true, state: state, includeDelivery: false)
        precondition(preparedCommand.ready)
        let changed = NativeReceiverSnapshot(
            windowRoute: "yes", focus: other, ancestors: [], foreground: false, window: window, surface: window)
        let wrongReceiver = try unit.readiness(
            kind: .key, key: "super+a", prepared: true, state: changed, includeDelivery: false)
        precondition(!wrongReceiver.ready, "a prepared window still cannot authorize another focused field")
    }

    @MainActor static func firstKeyDownConsumesBeforeAwait() async throws {
        let gate = Input.FirstKeyDown()
        var trace = [String]()
        try await gate.run {
            trace.append("handoff")
            try await gate.run { trace.append("reentrant-handoff") }
            await Task.yield()
            trace.append("returned")
        }
        trace.append("first-key")
        try await gate.run { trace.append("repeated-handoff") }
        trace.append("remaining-key")
        precondition(trace == ["handoff", "returned", "first-key", "remaining-key"])

        let absent = Input.FirstKeyDown()
        try await absent.run(nil)
        try await absent.run { preconditionFailure("a consumed first-down slot cannot gain a later handoff") }
    }

    @MainActor static func firstKeyFailureDoesNotRetry(cancelled: Bool) async throws {
        let gate = Input.FirstKeyDown()
        var calls = 0
        do {
            try await gate.run {
                calls += 1
                if cancelled { throw CancellationError() }
                throw Stop.failed
            }
            preconditionFailure("handoff failure must propagate before key dispatch")
        } catch {
            precondition(cancelled ? error is CancellationError : error is Stop)
        }
        try await gate.run { calls += 1 }
        precondition(calls == 1, "failed or cancelled first-down preparation must not be retried")
    }

    @MainActor static func commandPreparationNeedsPositiveEvidence() throws {
        typealias Evidence = NativeKeyboardCommandContext.ClipboardEvidence
        precondition(Evidence(standardWindow: true, windowFocused: false, windowFocusWritable: false,
                              applicationFocusWritable: false).needsForeground)
        for evidence in [
            Evidence(standardWindow: false, windowFocused: false, windowFocusWritable: false,
                     applicationFocusWritable: false),
            Evidence(standardWindow: true, windowFocused: nil, windowFocusWritable: false,
                     applicationFocusWritable: false),
            Evidence(standardWindow: true, windowFocused: false, windowFocusWritable: false,
                     applicationFocusWritable: nil),
            Evidence(standardWindow: true, windowFocused: false, windowFocusWritable: true,
                     applicationFocusWritable: false)
        ] { precondition(!evidence.needsForeground) }
        let window = AXUIElementCreateApplication(91071)
        let other = AXUIElementCreateApplication(91072)
        let wrong = NativeReceiverSnapshot(windowRoute: "yes", focus: nil, ancestors: [], foreground: true,
                                           window: window, focusedWindowRead: .value(other))
        do {
            try NativeKeyboardCommandContext.requireForeground(wrong, window: window, exactKey: true)
            preconditionFailure("clipboard command admitted another key window")
        } catch let error as DriverError { precondition(error.code == .inputUnavailable) }
        let ready = NativeReceiverSnapshot(windowRoute: "yes", focus: nil, ancestors: [], foreground: true,
                                           window: window, focusedWindowRead: .value(window))
        try NativeKeyboardCommandContext.requireForeground(ready, window: window, exactKey: true)
    }

    @MainActor static func coldPanelProofDoesNotBorrowAHostedOrUnrelatedReceiver() throws {
        let panel = AXUIElementCreateApplication(91071)
        let child = AXUIElementCreateApplication(91072)
        let other = AXUIElementCreateApplication(91073)
        let mapping = WindowGeometry.WindowMapping(id: 101, pid: 91071, frame: .zero, title: nil, width: 1, height: 1)
        var children = [child]
        var ownsPanel = true
        var hosted = false
        var kind: RemoteApplicationKind = .prohibitedApplication
        var query = NativeKeyboardRecipient.Queries()
        query.actualPID = { CFEqual($0, child) ? 91072 : 91073 }
        query.belongs = { _, window in ownsPanel && window.id == mapping.id }
        query.endpoint = { object in
            .init(element: object, pid: CFEqual(object, child) ? 91072 : 91073, kind: kind)
        }
        query.process = { .init(pid: $0, parent: 1, seconds: 2, microseconds: 0, executable: "service") }
        query.windowID = { _ in hosted ? mapping.id : nil }
        query.webArea = { _, _, _ in hosted ? child : nil }
        query.focus = { _ in preconditionFailure("cold panel preparation must not depend on old focus") }
        func sample() throws -> Bool {
            try NativeKeyboardCommandContext.coldService(
                window: panel, mapping: mapping, children: { _ in children }, role: { _ in kAXSheetRole }, query: query)
        }
        let proven = try sample()
        precondition(proven)
        ownsPanel = false
        let foreign = try sample()
        precondition(!foreign)
        ownsPanel = true
        hosted = true
        let relay = try sample()
        precondition(!relay)
        hosted = false
        kind = .ordinaryApplication
        let ordinary = try sample()
        precondition(!ordinary)
        kind = .prohibitedApplication
        children = [child, other]
        let ambiguous = try sample()
        precondition(!ambiguous)
    }

    @MainActor static func clipboardRetainsOnlyItsOwnKeyWindowRequirement() throws {
        let window = AXUIElementCreateApplication(91071)
        let other = AXUIElementCreateApplication(91072)
        let attached = NativeReceiverSnapshot(windowRoute: "yes", focus: window, ancestors: [], foreground: true,
                                              window: window, focusedWindowRead: .unsupported)
        let responsibility = NativeForegroundResponsibility(watchActivation: false)
        responsibility.beginCandidate("paste")
        responsibility.requireForegroundForCandidate()
        // ForegroundAssist is shared by sheet/attached receiver preparation.
        // Its generic obligation cannot impose AXFocusedWindow on this route.
        try NativeKeyboardCommandContext.validateClipboard(
            attached, window: window, requiredKeyWindow: responsibility.requiredKeyWindow,
            needsForeground: { preconditionFailure("frontmost receiver does not need another native probe") })
        responsibility.requireForegroundForCandidate(keyWindow: window)
        responsibility.requireForegroundForCandidate()
        responsibility.beginCandidate("paste")
        do {
            try NativeKeyboardCommandContext.validateClipboard(
                attached, window: window, requiredKeyWindow: responsibility.requiredKeyWindow,
                needsForeground: { false })
            preconditionFailure("an explicitly required key window cannot be confirmed by focused-root evidence")
        } catch let error as DriverError { precondition(error.code == .inputUnavailable) }
        let ready = NativeReceiverSnapshot(windowRoute: "yes", focus: nil, ancestors: [], foreground: true,
                                           window: window, focusedWindowRead: .value(window))
        try NativeKeyboardCommandContext.validateClipboard(
            ready, window: window, requiredKeyWindow: responsibility.requiredKeyWindow, needsForeground: { false })
        do {
            try NativeKeyboardCommandContext.validateClipboard(
                ready, window: other, requiredKeyWindow: responsibility.requiredKeyWindow, needsForeground: { false })
            preconditionFailure("a key-window requirement cannot transfer to another target")
        } catch let error as DriverError { precondition(error.code == .staleTarget) }
        responsibility.beginCandidate("other")
        precondition(responsibility.requiredKeyWindow == nil)
        let background = NativeReceiverSnapshot(windowRoute: "yes", focus: window, ancestors: [], foreground: false,
                                                window: window, focusedWindowRead: .value(window))
        try NativeKeyboardCommandContext.validateClipboard(
            background, window: window, requiredKeyWindow: nil, needsForeground: { false })
        do {
            try NativeKeyboardCommandContext.validateClipboard(
                background, window: window, requiredKeyWindow: nil, needsForeground: { true })
            preconditionFailure("positive late loss of the actual key window cannot pass a synthetic preparation receipt")
        } catch let error as DriverError { precondition(error.code == .inputUnavailable) }
    }

    @MainActor static func selectedRangeRemainsInTheOriginalTable() throws {
        let window = AXUIElementCreateApplication(91071)
        let table = AXUIElementCreateApplication(91072)
        let first = AXUIElementCreateApplication(91073)
        let second = AXUIElementCreateApplication(91074)
        let otherTable = AXUIElementCreateApplication(91075)
        var rows = [first, second]
        var focusOwner = table
        var currentFocus = second
        var currentWindow = window
        let access = NativeSelectionKeyReceiver.Access(
            read: { node, key in
                switch key {
                case kAXRoleAttribute:
                    return .value((CFEqual(node, table) || CFEqual(node, otherTable) ? kAXTableRole : kAXRowRole) as CFString)
                case kAXWindowAttribute: return .value(window)
                case kAXFocusedWindowAttribute: return .value(currentWindow)
                case kAXFocusedUIElementAttribute: return .value(currentFocus)
                case kAXFocusedAttribute: return .value(kCFBooleanTrue)
                case kAXParentAttribute: return .value(CFEqual(node, second) ? focusOwner : table)
                default: return .unsupported
                }
            }, selectedRows: { _ in rows }, pid: { _ in 91071 })
        let state = NativeReceiverSnapshot(windowRoute: "yes", focus: second, ancestors: [], foreground: false,
                                           window: window, rawFocus: second, rawFocusOwner: window, rawLocalFocus: true)
        func sample() throws -> NativeSelectionKeyReceiver? {
            try NativeSelectionKeyReceiver.capture(original: first, state: state, window: window, pid: 91071, using: access)
        }
        precondition(NativeSelectionKeyReceiver.accepts(.key, key: "shift+Down"))
        precondition(!NativeSelectionKeyReceiver.accepts(.key, key: "Down"))
        precondition(!NativeSelectionKeyReceiver.accepts(.insert, key: "shift+Down"))
        let proof = try sample()
        let initial = try sample()
        precondition(proof?.matches(initial) == true, "range navigation may retain the selected original row")
        rows = [second]
        let deselected = try sample()
        precondition(deselected == nil)
        rows = [first, second]
        focusOwner = otherTable
        let foreign = try sample()
        precondition(foreign == nil)
        focusOwner = table
        currentFocus = first
        let focusMovedAfterSampling = try sample()
        precondition(focusMovedAfterSampling == nil, "the sampled receiver cannot authorize keys after focus changes")
        currentFocus = second
        currentWindow = otherTable
        let keyWindowMovedAfterSampling = try sample()
        precondition(keyWindowMovedAfterSampling == nil, "unchanged selection cannot authorize keys in another key window")
    }

    @MainActor static func qualifiedLocalInsertionKeepsTheOriginalSelectionAndScope() {
        let window = AXUIElementCreateApplication(91071)
        let editor = AXUIElementCreateApplication(91072)
        let document = AXUIElementCreateApplication(91073)
        let other = AXUIElementCreateApplication(91074)
        let proof = NativeGeckoTextProvider.Proof(
            element: editor, document: document, window: window, pid: NSRunningApplication.current.processIdentifier)
        var unit = NativeKeyboardUnit(application: .current, window: window, element: editor)
        unit.originalAddressedElement = true
        unit.keyboardInsertion = .init(role: kAXTextFieldRole, subrole: kAXSearchFieldSubrole)
        let local = NativeKeyboardInterpreterEvidence.Observation(kind: .localWeb, area: document, complete: true)
        precondition(!unit.permitsGeckoInsertion(.insert, window: window, interpreter: local))
        precondition(NativeKeyboardDeliveryProposal(interpreter: local).processTextUnavailable,
                     "ordinary local web text transport stays restricted")
        unit.geckoInsertion = proof
        precondition(unit.permitsGeckoInsertion(.insert, window: window, interpreter: local))
        precondition(!unit.permitsGeckoInsertion(.replace, window: window, interpreter: local))
        precondition(!unit.permitsGeckoInsertion(.paste, window: window, interpreter: local))
        precondition(!unit.permitsGeckoInsertion(.insert, window: other, interpreter: local))
        precondition(!unit.permitsGeckoInsertion(
            .insert, window: window, interpreter: .init(kind: .localWeb, area: other, complete: true)))
        precondition(!unit.permitsGeckoInsertion(
            .insert, window: window, interpreter: .init(kind: .observedWebOwnershipUnknown, area: document)))
        unit.originalAddressedElement = false
        precondition(!unit.permitsGeckoInsertion(.insert, window: window, interpreter: local))

        let empty = NativeTextSelection(count: 0, location: 0, length: 0)
        let start = NativeTextSelection(count: 4, location: 0, length: 0)
        let caret = NativeTextSelection(count: 4, location: 2, length: 0)
        let selection = NativeTextSelection(count: 4, location: 0, length: 2)
        precondition(!NativeKeyboardUnit.needsGeckoInsertion(selection: empty, retained: nil))
        precondition(!NativeKeyboardUnit.needsGeckoInsertion(selection: start, retained: nil))
        precondition(NativeKeyboardUnit.needsGeckoInsertion(selection: caret, retained: nil))
        precondition(NativeKeyboardUnit.needsGeckoInsertion(selection: selection, retained: nil))
        precondition(NativeKeyboardUnit.needsGeckoInsertion(selection: nil, retained: nil))
        precondition(NativeKeyboardUnit.needsGeckoInsertion(selection: start, retained: caret),
                     "a changed position must not select the native setter's offset-zero route")
    }

    @MainActor static func comboFocusDoesNotRequireATextContract() {
        precondition(NativePointerFocusUnit.supportsEditorPreparation(role: kAXComboBoxRole, intent: .focus))
        for intent: NativeKeyboardIntent.Kind in [.insert, .replace, .paste, .key] {
            precondition(!NativePointerFocusUnit.supportsEditorPreparation(role: kAXComboBoxRole, intent: intent))
        }
    }

    @MainActor static func savePanelContinuesOnlyTheProvedUnsentSuffix() async throws {
        let objects = (91071...91077).map { AXUIElementCreateApplication(pid_t($0)) }
        let panel = objects[0], group = objects[1], original = objects[2]
        let sheet = objects[3], path = objects[4], foreign = objects[5], unrelatedSheet = objects[6]
        let presenter = NativeProcessIdentity(pid: 91071, parent: 1, seconds: 2, microseconds: 0, executable: "host")
        let service = NativeProcessIdentity(pid: 91072, parent: 1, seconds: 3, microseconds: 0, executable: "service")
        let mapping = WindowGeometry.WindowMapping(id: 100, pid: presenter.pid, frame: .zero,
                                                   title: nil, width: 1, height: 1)
        let endpoint = NativeKeyboardRecipient.Endpoint(element: original, pid: service.pid, kind: .prohibitedApplication)
        let recipient = NativeKeyboardRecipient(
            pid: service.pid, window: 101, remoteElement: original, presenter: presenter.pid,
            process: service, axWindow: 101, remoteRoute: .viewService(.init(window: mapping, process: presenter)),
            endpoint: endpoint, endpointProcess: service)
        var focus = original
        var value: String? = "/"
        var children = [group]
        var windowLink: AXUIElement?
        var sheetParent: CGWindowID = 100
        var access = NativeSavePanelPathContinuation.Access()
        access.publicPID = { _ in presenter.pid }
        access.owner = { [100: presenter.pid, 101: service.pid, 102: presenter.pid][$0] }
        access.ownedWindows = { _ in [100, 101] }
        access.parents = { _ in [102: sheetParent] }
        access.receiver.focus = { _ in focus }
        access.receiver.process = { [presenter.pid: presenter, service.pid: service][$0] }
        access.receiver.actualPID = { object in
            CFEqual(object, panel) || CFEqual(object, sheet) ? presenter.pid : service.pid
        }
        access.receiver.windowID = { object in
            if CFEqual(object, panel) { return 100 }
            if CFEqual(object, group) || CFEqual(object, original) { return 101 }
            if CFEqual(object, sheet) { return 102 }
            return nil
        }
        access.receiver.endpoint = { .init(element: $0, pid: service.pid, kind: .prohibitedApplication) }
        access.read = { object, key in
            guard let node = objects.firstIndex(where: { CFEqual($0, object) }) else { return .invalid }
            switch key {
            case kAXRoleAttribute:
                let roles = [kAXWindowRole, kAXSplitGroupRole, kAXTextFieldRole, kAXSheetRole,
                             kAXTextFieldRole, kAXTextFieldRole, kAXSheetRole]
                return .value(roles[node] as CFString)
            case kAXIdentifierAttribute:
                return .value(["save-panel", "", "saveAsNameTextField", "GoToWindow", "PathTextField", "", ""][node] as CFString)
            case kAXSubroleAttribute: return .value(kAXDialogSubrole as CFString)
            case kAXFocusedAttribute: return .value(CFEqual(object, focus) ? kCFBooleanTrue : kCFBooleanFalse)
            case kAXModalAttribute: return .value(kCFBooleanTrue)
            case kAXValueAttribute:
                guard let text = node == 2 ? "" : value else { return .unavailable(.noValue) }
                return .value(text as CFString)
            case kAXSelectedTextRangeAttribute:
                var range = CFRange(location: node == 2 ? 0 : value?.utf16.count ?? 0, length: 0)
                return .value(AXValueCreate(.cfRange, &range)!)
            case kAXChildrenAttribute:
                return .value((node == 0 ? children : node == 1 ? [original] : node == 3 ? [path] : []) as CFArray)
            case kAXParentAttribute:
                if node == 1 || node == 3 { return .value(panel) }
                if node == 2 { return .value(group) }
                if node == 4 { return .value(sheet) }
                return .unsupported
            case kAXWindowAttribute:
                return windowLink.map { .value($0) } ?? .unsupported
            default: return .unsupported
            }
        }
        func state(initial: Bool = false) -> NativeReceiverSnapshot {
            .init(windowRoute: initial ? "yes" : "no", focus: initial ? original : nil,
                  ancestors: [], foreground: false, window: panel, rawFocus: focus, rawLocalFocus: true)
        }
        func capture() throws -> NativeSavePanelPathContinuation? {
            try NativeSavePanelPathContinuation.capture(window: panel, element: original, text: "/tmp/a",
                                                         state: state(initial: true), delivery: .process(recipient), access: access)
        }
        children = [group, sheet]
        let withPreexistingSheet = try capture()
        precondition(withPreexistingSheet == nil)
        children = [group]
        let continuation = try capture()!
        children = [group, sheet]
        focus = path
        let beforeRelease = try continuation.resolve(postedPrefix: "", state: state())
        let mismatchedPrefix = try continuation.resolve(postedPrefix: "/t", state: state())
        precondition(beforeRelease == nil && mismatchedPrefix == nil)
        sheetParent = 999
        let foreignParent = try continuation.resolve(postedPrefix: "/", state: state())
        precondition(foreignParent == nil)
        sheetParent = 100
        windowLink = foreign
        let conflictingWindowLink = try continuation.resolve(postedPrefix: "/", state: state())
        precondition(conflictingWindowLink == nil)
        windowLink = nil
        let proof = try continuation.resolve(postedPrefix: "/", state: state())!
        precondition(proof.authorizes(recipient, lifetime: service))
        precondition(!proof.authorizes(recipient, lifetime: presenter))
        precondition(proof.recipient.window == nil && CFEqual(proof.recipient.remoteElement, path))
        try proof.validateFocus()
        let rows: [NativeApplicationAccess.Window] = [
            .init(ref: "panel", title: "Save", element: panel, nativeID: 100, modal: true),
            .init(ref: "sheet", title: "Go To", element: sheet, kind: "sheet", parent: "panel", nativeID: 102, modal: true)
        ]
        let blockers = try proof.blocked(in: rows, root: "panel", focused: "sheet")
        precondition(blockers.isEmpty)
        let otherModal = NativeApplicationAccess.Window(
            ref: "other", title: "Other", element: unrelatedSheet, kind: "sheet", parent: "sheet", nativeID: 103, modal: true)
        let extraBlockers = try proof.blocked(in: rows + [otherModal], root: "panel", focused: "other")
        precondition(extraBlockers == ["other"])
        value = nil
        let asynchronousValue = try continuation.resolve(postedPrefix: "/tmp", state: state())
        precondition(asynchronousValue != nil, "the retained receiver may publish AXValue asynchronously")
        focus = foreign
        let moved = try continuation.resolve(postedPrefix: "/tmp/", state: state())
        precondition(moved == nil)
        do {
            try proof.validateFocus()
            preconditionFailure("late focus change must stop the remaining input")
        } catch let error as DriverError { precondition(error.code == .staleTarget) }

        // A released slash triggers a new sheet. The old receiver can still be
        // current when the next scalar asks for a proof.
        focus = original
        value = "/"
        children = [group]
        let delayed = try capture()!
        var time: TimeInterval = 0, reads = 0
        let resolved = try await Input.savePanelPathReceiver(
            after: "/", now: { time }, pause: {
                time += $0
                children = [group, sheet]
                focus = path
            }, resolve: { prefix in
                reads += 1
                return try delayed.resolve(postedPrefix: prefix, state: state())
            })
        precondition(resolved?.authorizes(recipient, lifetime: service) == true && reads == 2,
                     "the next scalar must wait for the exact receiver after the released slash")

        time = 0
        reads = 0
        do {
            _ = try await Input.savePanelPathReceiver(
                after: "/", now: { time }, pause: { time += $0 }, resolve: { _ in reads += 1; return nil })
            preconditionFailure("an unproved handoff must not fall back to the old name field")
        } catch let error as DriverError {
            precondition(error.code == .inputUnavailable && time > 0 && time <= 0.25 && reads > 1)
        }
        do {
            _ = try await Input.savePanelPathReceiver(
                after: "/", pause: { _ in throw CancellationError() }, resolve: { _ in nil })
            preconditionFailure("cancellation must stop before another scalar")
        } catch is CancellationError {}
        for prefix in ["", "/t", "//"] {
            reads = 0
            _ = try await Input.savePanelPathReceiver(after: prefix, pause: { _ in
                preconditionFailure("only the first released slash may wait for a handoff")
            }, resolve: { checked in
                precondition(checked == prefix)
                reads += 1
                return nil
            })
            precondition(reads == 1)
        }
    }

    @MainActor static func cellSelectionUsesOneAddressedPreparation() async throws {
        let world = CellSelectionWorld()
        world.initialSelection(2)
        world.automaticFocus = false
        let unit = world.unit()
        let contract = try unit.offer()!
        precondition(world.sets.isEmpty)
        let offer = NativeInputUnit.cellSelection(contract).declaration(
            id: "original", supported: true, readiness: .init([("receiver", "unknown")]))
        precondition(offer["unit"] as? String == "prepare_receiver")
        precondition(offer["channel"] as? String == "editor")
        precondition(offer["backgroundPreparation"] as? String == "yes")
        var effects = [String]()
        try await unit.prepare(contract, check: {}, onPreparation: { effects.append($0) })
        precondition(world.sets == ["AXSelectedCells", "AXFocused"] && effects.count == 2)
        let offeredAgain = try unit.offer()
        precondition(offeredAgain == nil)

        let settling = CellSelectionWorld()
        settling.automaticFocus = false
        settling.focusWritable = false
        settling.settleFocusAfterSelectionRead = true
        let settlingUnit = settling.unit(), settlingContract = try settlingUnit.offer()!
        try await settlingUnit.prepare(settlingContract, check: {}, onPreparation: { _ in })
        precondition(settling.sets == ["AXSelectedCells"])

        let ambiguous = CellSelectionWorld()
        ambiguous.afterSet = "selectedEditorWithTableFocus"
        let ambiguousUnit = ambiguous.unit(), ambiguousContract = try ambiguousUnit.offer()!
        do {
            try await ambiguousUnit.prepare(ambiguousContract, check: {}, onPreparation: { _ in })
            preconditionFailure("a competing selected editor cannot inherit the original cell")
        } catch let error as DriverError { precondition(error.effect == .possible) }
        precondition(ambiguous.sets == ["AXSelectedCells"])
    }

    static func explicitAXSelectionWritabilityDoesNotExpandDefaultReads() {
        for field in ["settable.AXSelectedRows", "settable.AXSelectedChildren"] {
            precondition(AXAttributeProfile.field(field) && AXAttributeProfile.writableAttribute(field) != nil)
            precondition(!AXAttributeProfile.defaultWritableFields.contains(field))
        }
        precondition(!AXAttributeProfile.field("settable.AXUnknown"))
    }

    @MainActor static func compoundTextReturnRequiresExactLiveOwner() throws {
        let window = AXUIElementCreateApplication(91071)
        let owner = AXUIElementCreateApplication(91072)
        let field = AXUIElementCreateApplication(91073)
        let other = AXUIElementCreateApplication(91074)
        let text = "00:00:09.000"
        var children: [AXUIElement]? = [field]
        var ownerWindow = window, focused = owner, delegated = text
        var state = NativeReceiverSnapshot(
            windowRoute: "yes", focus: owner, ancestors: [], foreground: false, window: window,
            rawFocus: owner, rawFocusOwner: window, rawLocalFocus: true)
        var access = NativeCompositeTextKeyReceiver.Access()
        access.read = { node, fieldKey in
            if fieldKey == kAXRoleAttribute {
                return .value((CFEqual(node, owner) ? kAXIncrementorRole : kAXTextFieldRole) as CFString)
            }
            if fieldKey == kAXValueAttribute {
                precondition(CFEqual(node, field), "the compound control's scalar value is not the editor text")
                return .value(text as CFString)
            }
            if fieldKey == kAXNumberOfCharactersAttribute { return .value(NSNumber(value: text.utf16.count)) }
            if fieldKey == kAXFocusedAttribute {
                return .value(CFEqual(node, owner) ? kCFBooleanTrue : kCFBooleanFalse)
            }
            return .unsupported
        }
        access.valueWritable = { CFEqual($0, field) }
        access.parent = { node in CFEqual(node, field) ? owner : CFEqual(node, owner) ? ownerWindow : nil }
        access.children = { _ in children }
        access.pid = { _ in 91071 }
        access.focus = { _ in focused }
        access.string = { _, count in count == text.utf16.count ? delegated : nil }
        func capture() throws -> NativeCompositeTextKeyReceiver? {
            try .capture(field: field, state: state, window: window, pid: 91071, using: access)
        }
        let proof = try capture()
        precondition(proof != nil && state.receiver(field) == "no")
        precondition(NativeCompositeTextKeyReceiver.accepts(.key, key: "Return"))
        precondition(!NativeCompositeTextKeyReceiver.accepts(.key, key: "BackSpace"))
        precondition(!NativeCompositeTextKeyReceiver.accepts(.replace, key: nil))
        for invalid in [[field, other], [other], nil] {
            children = invalid
            let missing = try capture()
            precondition(missing == nil, "child membership must be complete and exact")
        }
        children = [field]
        delegated = "00:00:03.000"
        let mismatch = try capture()
        precondition(mismatch == nil)
        delegated = text
        ownerWindow = other
        let moved = try capture()
        precondition(moved == nil)
        ownerWindow = window
        focused = other
        let changed = try capture()
        precondition(changed == nil, "provider queries can change the final reported focus")
        focused = owner
        state.rawFocusOwner = other
        let outside = try capture()
        precondition(outside == nil)
    }

    @MainActor static func main() async throws {
        try compoundTextReturnRequiresExactLiveOwner()
        onlyConfirmedLocalReceiverQualifies()
        try windowCommandsKeepTheirPreparation()
        try commandPreparationNeedsPositiveEvidence()
        try clipboardRetainsOnlyItsOwnKeyWindowRequirement()
        try coldPanelProofDoesNotBorrowAHostedOrUnrelatedReceiver()
        try selectedRangeRemainsInTheOriginalTable()
        comboFocusDoesNotRequireATextContract()
        qualifiedLocalInsertionKeepsTheOriginalSelectionAndScope()
        try await savePanelContinuesOnlyTheProvedUnsentSuffix()
        try await cellSelectionUsesOneAddressedPreparation()
        explicitAXSelectionWritabilityDoesNotExpandDefaultReads()
        try await firstKeyDownConsumesBeforeAwait()
        try await firstKeyFailureDoesNotRetry(cancelled: false)
        try await firstKeyFailureDoesNotRetry(cancelled: true)
        print("PASS confirmed local keyboard receiver preserves window-command obligations; first-down handoff is consumed once")
    }
}

@MainActor private final class CellSelectionWorld {
    let application = NSRunningApplication.current
    let cell = AXUIElementCreateApplication(71001), row = AXUIElementCreateApplication(71002)
    let table = AXUIElementCreateApplication(71003), region = AXUIElementCreateApplication(71004)
    let window = AXUIElementCreateApplication(71005), other = AXUIElementCreateApplication(71006)
    var values = [UInt: [String: CFTypeRef]]()
    var count: Int? = 0
    var focus: AXUIElement?
    var parameter: AXUIElement?
    var selectionWritable: Bool? = true
    var focusWritable: Bool? = true
    var ownerPID: pid_t?
    var automaticFocus = true
    var settleFocusAfterSelectionRead = false
    var settledFocus = false
    var afterSet: String?
    var sets = [String]()

    init() {
        ownerPID = application.processIdentifier
        focus = region; parameter = cell
        for (element, role) in [(cell, "AXCell"), (row, "AXRow"), (table, "AXTable"), (region, "AXLayoutArea")] {
            put(element, kAXRoleAttribute, role as CFString)
            put(element, kAXEnabledAttribute, kCFBooleanTrue)
            put(element, kAXWindowAttribute, window)
        }
        put(cell, kAXParentAttribute, row); put(row, kAXParentAttribute, table); put(table, kAXParentAttribute, region)
        put(cell, "AXRowIndexRange", range(1)); put(cell, "AXColumnIndexRange", range(2))
        put(cell, kAXSelectedAttribute, kCFBooleanFalse)
        put(region, kAXFocusedAttribute, kCFBooleanTrue)
        put(region, kAXSelectedChildrenAttribute, [] as CFArray)
        put(table, "AXSelectedCells", [] as CFArray)
    }
    func range(_ location: Int) -> AXValue {
        var range = CFRange(location: location, length: 1)
        return AXValueCreate(.cfRange, &range)!
    }
    func put(_ element: AXUIElement, _ key: String, _ value: CFTypeRef?) {
        values[CFHash(element), default: [:]][key] = value
    }
    func initialSelection(_ size: Int) {
        count = size < 0 ? nil : size
        put(table, "AXSelectedCells", size < 0 ? nil : Array(repeating: other, count: size) as CFArray)
    }
    func selectOriginal(active: Bool) {
        count = 1
        put(table, "AXSelectedCells", [cell] as CFArray)
        put(cell, kAXSelectedAttribute, kCFBooleanTrue)
        if active { focus = table; put(table, kAXFocusedAttribute, kCFBooleanTrue) }
    }
    func invalidate(_ kind: String) {
        switch kind {
        case "readonly": put(cell, "AXReadOnly", kCFBooleanTrue)
        case "noteditable": put(cell, "AXEditable", kCFBooleanFalse)
        case "tableReadonly": put(table, "AXReadOnly", kCFBooleanTrue)
        case "disabled": put(cell, kAXEnabledAttribute, kCFBooleanFalse)
        case "writableFalse": selectionWritable = false
        case "writableUnknown": selectionWritable = nil
        case "row": put(cell, kAXParentAttribute, other)
        case "table": put(row, kAXParentAttribute, other)
        case "window": put(cell, kAXWindowAttribute, other)
        case "pid": ownerPID = application.processIdentifier + 1
        case "position": put(cell, "AXColumnIndexRange", range(3))
        case "parameter": parameter = other
        case "otherFocus": focus = other
        case "unknownFocus": focus = nil
        case "selectedEditor": put(region, kAXSelectedChildrenAttribute, [other] as CFArray)
        case "selectedEditorWithTableFocus":
            focus = table; put(table, kAXFocusedAttribute, kCFBooleanTrue)
            put(region, kAXSelectedChildrenAttribute, [other] as CFArray)
        case "unknownRegionSelection": put(region, kAXSelectedChildrenAttribute, nil)
        case "cellDestroyed": put(cell, kAXRoleAttribute, nil)
        case "otherSelection": count = 1; put(table, "AXSelectedCells", [other] as CFArray); put(cell, kAXSelectedAttribute, kCFBooleanFalse)
        case "multipleSelection": count = 2; put(table, "AXSelectedCells", [cell, other] as CFArray)
        case "unknownSelection": count = nil; put(table, "AXSelectedCells", nil)
        case "contradictorySelection":
            count = 1; put(table, "AXSelectedCells", [other] as CFArray)
            put(other, kAXRoleAttribute, "AXCell" as CFString)
            put(other, "AXRowIndexRange", range(1)); put(other, "AXColumnIndexRange", range(3))
        default: break
        }
    }
    func unit() -> NativeCellSelectionPreparation {
        var api = NativeCellSelectionSystem.API()
        api.read = { self.values[CFHash($0)]?[$1] }
        api.writable = { _, key in key == "AXSelectedCells" ? self.selectionWritable : self.focusWritable }
        api.count = { element, key in
            if CFEqual(element, self.table) && key == "AXSelectedCells" { return self.count }
            return (self.values[CFHash(element)]?[key] as? [AXUIElement])?.count
        }
        api.pid = { _ in self.ownerPID }
        api.cell = { _, column, row in column == 2 && row == 1 ? self.parameter : nil }
        api.receiver = { _, _ in
            let observed = NativeReceiverSnapshot(
                windowRoute: "yes", focus: self.focus, ancestors: [], foreground: true,
                window: self.window, surface: self.window, scopeRoute: "yes")
            if self.settleFocusAfterSelectionRead && self.count == 1 && !self.settledFocus {
                self.settledFocus = true
                self.focus = self.table; self.put(self.table, kAXFocusedAttribute, kCFBooleanTrue)
            }
            return observed
        }
        api.wait = {}
        api.set = { element, key, value in
            precondition(CFEqual(element, self.table), "never mutate another cell/table/editor")
            self.sets.append(key as String)
            if key as String == "AXSelectedCells" {
                let cells = value as! [AXUIElement]
                precondition(cells.count == 1 && CFEqual(cells[0], self.cell), "exact original Cell only")
                self.selectOriginal(active: self.automaticFocus)
                if let failure = self.afterSet { self.invalidate(failure) }
                if self.afterSet == "setterFailure" { return .failure }
            } else {
                precondition(key as String == kAXFocusedAttribute)
                self.focus = self.table; self.put(self.table, kAXFocusedAttribute, kCFBooleanTrue)
            }
            return .success
        }
        return .init(application: application, window: window, element: cell, api: api)
    }
}
