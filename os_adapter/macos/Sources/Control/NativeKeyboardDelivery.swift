// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices
import Carbon

/// A call-local proposal. It is not an input permission or a text-client proof.
@MainActor struct NativeKeyboardDeliveryProposal {
    let interpreter: NativeKeyboardInterpreterEvidence.Observation<AXUIElement>
    var permitsQualifiedLocalInsertion = false
    var processTextUnavailable: Bool { interpreter.kind == .localWeb && !permitsQualifiedLocalInsertion }
    var unavailable: Bool { interpreter.kind == .observedWebOwnershipUnknown }

    var refusalReason: String? {
        if processTextUnavailable { return "Scoped Unicode typing is unavailable for the selected local Web receiver." }
        if unavailable { return "Scoped Unicode typing could not verify the selected Web receiver's text interpreter." }
        return nil
    }

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

/// Object input retains an addressed process recipient. Explicit Computer
/// input posts directly to the session without an object or focus contract.
@MainActor enum NativeKeyboardDelivery {
    case process(BackgroundEvents.KeyboardRecipient)
    case desktop

    var processRecipient: BackgroundEvents.KeyboardRecipient? {
        if case .process(let value) = self { return value }
        return nil
    }
    var isGlobal: Bool {
        if case .desktop = self { return true }
        return false
    }

    func requireAdmission() throws {
        switch self {
        case .process: try BackgroundEvents.requireKeyboardRoute()
        case .desktop: try Input.requireDesktopInput()
        }
    }

    func post(_ event: CGEvent) {
        switch self {
        case .process(let recipient): BackgroundEvents.postKey(event, to: recipient)
        case .desktop:
            let activity = NativeInputActivity.begin(
                device: .keyboard, transport: .globalHID, mechanism: "CGEvent.post.cghidEventTap", event: event)
            event.post(tap: .cghidEventTap)
            activity?.returned()
        }
    }

    /// Release remains the captured process or session; no current-PID retarget.
    func canRelease(expectedProcess: NativeProcessIdentity?) -> Bool {
        switch self {
        case .process(let recipient): return NativeProcessIdentity.read(recipient.pid) == expectedProcess
        case .desktop: return true
        }
    }
}
