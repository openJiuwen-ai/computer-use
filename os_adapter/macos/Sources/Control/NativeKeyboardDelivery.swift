// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices
import Carbon

/// A call-local proposal. It is not an input permission or a text-client proof.
@MainActor struct NativeKeyboardDeliveryProposal {
    let interpreter: NativeKeyboardInterpreterEvidence.Observation<AXUIElement>
    var needsForeground: Bool { interpreter.kind == .localWeb }
    var unavailable: Bool { interpreter.kind == .observedWebOwnershipUnknown }

    static func read(
        element: AXUIElement?, window: AXUIElement, pid: pid_t,
        policy: NativeKeyboardInterpreterEvidence.Policy = .existing
    ) throws -> Self {
        func owner(_ object: AXUIElement) -> pid_t? {
            var value: pid_t = 0
            return AXUIElementGetPid(object, &value) == .success ? value : nil
        }
        var observation = try NativeKeyboardInterpreterEvidence.classify(
            editor: element, window: window, presenter: pid, same: { CFEqual($0, $1) },
            role: { AXAccess.read($0, kAXRoleAttribute).value as? String }, owner: owner,
            actualPID: BackgroundEvents.accessibilityProcessID,
            parent: { object in
                switch AXAccess.read(object, kAXParentAttribute) {
                case .value(let value) where CFGetTypeID(value) == AXUIElementGetTypeID():
                    return .value(unsafeDowncast(value, to: AXUIElement.self))
                case .unsupported: return .queryUnavailable(.unsupported)
                case .unavailable(.noValue): return .queryUnavailable(.noValue)
                case .unavailable(.cannotComplete): return .queryUnavailable(.cannotComplete)
                default: return .unavailable
                }
            }, check: RequestDiagnostics.checkCancellation)
        if policy != .localWebNoProcess, let partial = observation.remoteContinuation {
            // Reuse the existing explicit membership algorithm on both original
            // objects. Only this typed remote/query-unavailable edge pays for it.
            try RequestDiagnostics.checkCancellation()
            let editorWindow = WindowGeometry.owningWindow(partial.editor, pid: pid, in: window)
            try RequestDiagnostics.checkCancellation()
            let areaWindow = WindowGeometry.owningWindow(partial.area, pid: pid, in: window)
            func scope(_ result: AXUIElement?) -> NativeKeyboardInterpreterEvidence.ContinuationScope {
                guard let result else { return .unavailable }
                return CFEqual(result, window) ? .original : .other
            }
            observation = NativeKeyboardInterpreterEvidence.supplementRemote(
                observation, policy: policy,
                facts: .init(
                    editorScope: scope(editorWindow), areaScope: scope(areaWindow),
                    editorPublicPID: owner(partial.editor), areaPublicPID: owner(partial.area),
                    editorActualPID: BackgroundEvents.accessibilityProcessID(partial.editor),
                    areaActualPID: BackgroundEvents.accessibilityProcessID(partial.area)))
            try RequestDiagnostics.checkCancellation()
        }
        return .init(interpreter: observation)
    }
}

/// App-scoped HID never passes through the optional desktop recipient path.
/// The original Unit still owns boundary, receiver and continuity validation.
@MainActor enum NativeKeyboardDelivery {
    case process(BackgroundEvents.KeyboardRecipient)
    case scopedForegroundHID(NativeScopedKeyboardDelivery)
    case exclusiveDesktop

    var processRecipient: BackgroundEvents.KeyboardRecipient? {
        if case .process(let value) = self { return value }
        return nil
    }
    var isScopedHID: Bool {
        if case .scopedForegroundHID = self { return true }
        return false
    }
    var isGlobal: Bool {
        if case .process = self { return false }
        return true
    }

    func requireAdmission() throws {
        switch self {
        case .process: try BackgroundEvents.requireKeyboardRoute()
        case .scopedForegroundHID(let value): try value.requireCurrentBoundary()
        case .exclusiveDesktop: try Input.requireDesktopInput()
        }
    }

    func post(_ event: CGEvent, isRelease: Bool, onCleanupUnknown: () -> Void) {
        switch self {
        case .process(let recipient): BackgroundEvents.postKey(event, to: recipient)
        case .scopedForegroundHID(let scoped):
            if isRelease, !scoped.sameForegroundLifetime() { onCleanupUnknown() }
            var count = 0, units = [UniChar](repeating: 0, count: 8)
            event.keyboardGetUnicodeString(
                maxStringLength: units.count, actualStringLength: &count, unicodeString: &units)
            diagnose(
                [
                    "kind": "scoped_keyboard_post", "delivery": "scopedForegroundHID",
                    "presenterPID": scoped.process.pid, "window": scoped.mapping.id, "eventType": event.type.rawValue,
                    "flags": event.flags.rawValue, "keycode": event.getIntegerValueField(.keyboardEventKeycode),
                    "unicodeUnits": count
                ] as [String: Any])
            event.post(tap: .cghidEventTap)
        case .exclusiveDesktop: event.post(tap: .cghidEventTap)
        }
    }

    /// Release remains the captured process or session; no current-PID retarget.
    func canRelease(expectedProcess: NativeProcessIdentity?) -> Bool {
        switch self {
        case .process(let recipient): return NativeProcessIdentity.read(recipient.pid) == expectedProcess
        case .scopedForegroundHID, .exclusiveDesktop: return true
        }
    }

    /// Only new scoped HID strips requested text from cleanup key-up. Desktop
    /// and existing process event encoding remain exactly their old recipes.
    func cleanup(_ up: CGEvent) {
        guard isScopedHID else { return }
        NativeKeyboardCleanup.scopedUp(up)
    }

}

@MainActor final class NativeScopedKeyboardDelivery {
    enum FocusedWindowEvidence { case target, other(CGWindowID?), unavailable(String) }
    let application: NSRunningApplication
    let window: AXUIElement
    let surface: AXUIElement
    let mapping: WindowGeometry.WindowMapping
    let editor: AXUIElement
    let area: AXUIElement
    let process: NativeProcessIdentity
    let readinessGate = NativeKeyboardReadinessGate()

    init(
        application: NSRunningApplication, window: AXUIElement, surface: AXUIElement,
        mapping: WindowGeometry.WindowMapping, editor: AXUIElement, area: AXUIElement, process: NativeProcessIdentity
    ) {
        self.application = application
        self.window = window
        self.surface = surface
        self.mapping = mapping
        self.editor = editor
        self.area = area
        self.process = process
    }

    func sameForegroundLifetime() -> Bool {
        NativeProcessIdentity.read(process.pid) == process
            && NSWorkspace.shared.frontmostApplication?.processIdentifier == process.pid
    }

    func requireCurrentBoundary() throws {
        try BackgroundTarget(application).validate(input: true)
        guard sameForegroundLifetime() else {
            throw DriverError(
                "the original scoped keyboard application is no longer foreground; remaining input was not sent",
                code: .staleTarget)
        }
    }

    /// Preserve AX status/type/native-ID absence as unknown, not other-window.
    func focusedWindow(_ read: AXRead<CFTypeRef>, status: AXError? = nil) -> FocusedWindowEvidence {
        switch read {
        case .unsupported:
            return .unavailable("AXFocusedWindow unsupported status \(status?.rawValue.description ?? "unknown")")
        case .invalid:
            return .unavailable("AXFocusedWindow invalid object status \(status?.rawValue.description ?? "unknown")")
        case .unavailable(let error): return .unavailable("AXFocusedWindow status \(error.rawValue)")
        case .value(let raw):
            guard CFGetTypeID(raw) == AXUIElementGetTypeID() else {
                return .unavailable("AXFocusedWindow invalid value type")
            }
            let current = unsafeDowncast(raw, to: AXUIElement.self)
            var owner: pid_t = 0
            guard AXUIElementGetPid(current, &owner) == .success else {
                return .unavailable("AXFocusedWindow owner unavailable")
            }
            guard owner == process.pid else { return .other(BackgroundEvents.nativeWindowID(current)) }
            let id = BackgroundEvents.nativeWindowID(current)
            if let id { return id == mapping.id ? .target : .other(id) }
            return CFEqual(current, window) ? .target : .unavailable("AXFocusedWindow native identity unavailable")
        }
    }

    func mechanismEvidence() throws -> NativeKeyboardReadinessGate.Evidence {
        try requireCurrentBoundary()
        guard let row = WindowServerInfo.window(mapping.id) else {
            return .unavailable("original scoped keyboard window query unavailable")
        }
        guard (row[kCGWindowNumber as String] as? NSNumber)?.uint32Value == mapping.id,
            (row[kCGWindowOwnerPID as String] as? NSNumber)?.int32Value == process.pid
        else { return .conflicting("original scoped keyboard window changed owner") }
        guard let bounds = row[kCGWindowBounds as String] as? [String: Any],
            let frame = CGRect(dictionaryRepresentation: bounds as CFDictionary),
            [frame.minX, frame.minY, frame.width, frame.height].allSatisfy(\.isFinite), frame.width > 0,
            frame.height > 0
        else { return .conflicting("original scoped keyboard window has invalid geometry") }
        guard (row[kCGWindowIsOnscreen as String] as? NSNumber)?.boolValue == true else {
            return .unavailable("original scoped keyboard window is not confirmed on screen")
        }
        let observation = try NativeKeyboardDeliveryProposal.read(
            element: editor, window: window, pid: process.pid, policy: .localWebNoProcess)
        switch observation.interpreter.kind {
        case .localWeb:
            guard observation.interpreter.area.map({ CFEqual($0, area) }) == true else {
                return .conflicting("original local-Web keyboard area changed")
            }
        case .remoteWeb: return .conflicting("original local-Web keyboard interpreter became remote")
        case .noObservedWeb:
            return observation.interpreter.complete
                ? .conflicting("original local-Web containment changed")
                : .unavailable("original local-Web parent query unavailable")
        case .observedWebOwnershipUnknown:
            return observation.interpreter.uncertainty == .identityConflict
                ? .conflicting("original local-Web keyboard ownership conflicts")
                : .unavailable("original local-Web keyboard ownership query unavailable")
        }
        return .ready
    }

    /// Last remote queries after boundary/selection/provider/relationship work.
    /// Compare to the exact receiver whose proof the Unit just validated.
    func finalReceiverEvidence(_ state: NativeReceiverSnapshot) throws -> NativeKeyboardReadinessGate.Evidence {
        let root = AXUIElementCreateApplication(process.pid)
        AXUIElementSetMessagingTimeout(root, 0.1)
        let focus = AXAccess.read(root, kAXFocusedUIElementAttribute)
        var rawWindow: CFTypeRef?
        let status = AXUIElementCopyAttributeValue(root, kAXFocusedWindowAttribute as CFString, &rawWindow)
        let window = AXRead<CFTypeRef>.result(status, rawWindow)
        guard sameForegroundLifetime() else {
            return .conflicting("scoped keyboard foreground changed during final receiver query")
        }
        let windowEvidence = focusedWindow(window, status: status)
        if case .other = windowEvidence {
            return .conflicting("scoped keyboard native window changed during final receiver query")
        }
        switch focus {
        case .value(let raw):
            guard CFGetTypeID(raw) == AXUIElementGetTypeID(), let expected = state.rawFocus ?? state.focus else {
                return .unavailable("final scoped keyboard focus has no exact receiver identity")
            }
            guard CFEqual(raw, expected) else {
                // A same-window popup/cell transition needs the original typed
                // proof resampled; do not either adopt or permanently reject it.
                return .unavailable(
                    "scoped keyboard receiver changed after proof; fresh original relationship proof required")
            }
        case .unsupported, .unavailable(.noValue):
            guard state.focusReportAbsent, AXAccess.read(editor, kAXFocusedAttribute).value as? Bool == true else {
                return .unavailable("final scoped keyboard focus is unavailable")
            }
        default: return .unavailable("final scoped keyboard focus query unavailable")
        }
        switch windowEvidence {
        case .target: return .ready
        case .other: return .conflicting("scoped keyboard native window changed")
        case .unavailable(let reason):
            if explicitReceiverWindowProof(state, currentRead: window) { return .ready }
            return .unavailable(reason)
        }
    }

    /// Exact same-object focus/owner is an alternative to an unavailable
    /// optional window getter, not permission to accept another known window.
    private func explicitReceiverWindowProof(_ state: NativeReceiverSnapshot, currentRead: AXRead<CFTypeRef>? = nil)
        -> Bool
    {
        let read: NativeKeyboardTransportPolicy.WindowRead
        switch currentRead ?? state.focusedWindowRead {
        case .unsupported?: read = .unsupported
        case .unavailable(.noValue)?: read = .noValue
        case .unavailable(.cannotComplete)?: read = .cannotComplete
        case .invalid?: read = .invalid
        default: read = .failed
        }
        return NativeKeyboardTransportPolicy.exactReceiverSubstitutesWindow(
            read, scope: state.scopeRoute == "yes", editor: state.rawFocus.map({ CFEqual($0, editor) }) == true,
            owner: state.rawFocusOwner.map({ CFEqual($0, window) }) == true, locallyFocused: state.rawLocalFocus == true
        )
    }

    func receiverState(_ state: NativeReceiverSnapshot) -> NativeReceiverSnapshot {
        guard state.windowRoute == "unknown", explicitReceiverWindowProof(state) else { return state }
        return .init(
            windowRoute: "yes", focus: state.rawFocus, ancestors: state.ancestors, foreground: sameForegroundLifetime(),
            relatedEditor: state.relatedEditor, window: state.window, surface: state.surface,
            scopeRoute: state.scopeRoute, reportedFocus: state.reportedFocus,
            focusReportAbsent: state.focusReportAbsent, focusedWindowRead: state.focusedWindowRead,
            focusedWindowStatus: state.focusedWindowStatus, rawFocus: state.rawFocus,
            rawFocusOwner: state.rawFocusOwner, rawLocalFocus: state.rawLocalFocus)
    }

    func windowEvidence(state: NativeReceiverSnapshot) -> NativeKeyboardReadinessGate.Evidence {
        guard sameForegroundLifetime() else {
            return .conflicting("scoped keyboard foreground or process lifetime changed during query")
        }
        guard let read = state.focusedWindowRead else { return .unavailable("focused-window evidence unavailable") }
        switch focusedWindow(read, status: state.focusedWindowStatus) {
        case .target: return .ready
        case .other: return .conflicting("another native window now receives scoped keyboard input")
        case .unavailable(let reason):
            if explicitReceiverWindowProof(state) { return .ready }
            // Existing declared attached-root proof is the only alternative.
            if state.focusReportAbsent == false, state.windowRoute == "yes", state.scopeRoute == "yes",
                state.window.map({ CFEqual($0, window) }) == true, state.focus.map({ CFEqual($0, window) }) == true
            {
                // The final fresh Snapshot already proved declared-root and
                // local focus; no new remote query may stale that snapshot.
                return .ready
            }
            return .unavailable(reason)
        }
    }
}
