// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices

/// Window-directed transport can address background content without global
/// activation. Readiness permits one delivery; its receipt leaves the actual
/// content effect unconfirmed until a subsequent observation.
enum NativePointerReadiness {
    enum Requirement { case contentReceiver, foregroundWindowPoint, preparedLocalPoint }
    enum WindowKind { case keyRequiring, otherDeclared, visualOnly, identityConflict }
    enum WindowFocus: Equatable {
        case target, otherWindow, absent, unresolved, failed, notRequired
        var reported: Bool { self == .target || self == .otherWindow }
    }

    /// A positive native mapping takes precedence over object equality. A
    /// missing optional bridge is not a conflicting identity.
    static func focusIdentity(nativeID: CGWindowID?, retainedEqual: Bool, targetID: CGWindowID) -> WindowFocus {
        if let nativeID { return nativeID == targetID ? .target : .otherWindow }
        return retainedEqual ? .target : .unresolved
    }

    /// Only the one-gesture prepared-point contract owns its preparation
    /// receipt. Provider deactivation is expected during foreground return;
    /// it may retire the shared cache without revoking this fixed destination.
    static func prepared(
        requirement: Requirement, cached: @autoclosure () -> Bool
    ) -> Bool {
        requirement == .preparedLocalPoint || cached()
    }

    /// A captured menu is rendered by its proven popup, even when the AX
    /// owning-window bridge names the document containing its opener.
    static func nativeWindowMatches(reported: CGWindowID?, expected: CGWindowID, presentedMenu: Bool) -> Bool? {
        if presentedMenu { return true }
        return reported.map { $0 == expected }
    }

    private enum Field: Equatable {
        case declared(String), absent, failed
        init(_ read: AXRead<CFTypeRef>) {
            switch read {
            case .unsupported, .unavailable(.noValue): self = .absent
            case .value(let value):
                if let value = value as? String {
                    self = value.isEmpty || value == kAXUnknownSubrole ? .absent : .declared(value)
                } else {
                    self = .failed
                }
            default: self = .failed
            }
        }
    }

    static func windowKind(
        role: AXRead<CFTypeRef>, subrole: AXRead<CFTypeRef>, nativeWindowMatches: Bool?,
        observed: [String: AXRead<CFTypeRef>]? = nil
    ) -> WindowKind {
        guard nativeWindowMatches != false else { return .identityConflict }
        let role = Field(role), subrole = Field(subrole)
        // Labels choose preparation, not window identity. Use a current
        // declaration even if its semantics changed; retain an original only
        // when the provider is temporarily unable to report the field.
        func declaration(_ field: Field, original: AXRead<CFTypeRef>?) -> String? {
            if case .declared(let value) = field { return value }
            if let original, case .declared(let value) = Field(original) { return value }
            return nil
        }
        guard let role = declaration(role, original: observed?[kAXRoleAttribute]) else { return .visualOnly }
        // Menus and declared content scopes need not expose an AX window ID.
        // Their retained capture/element boundary still owns the physical hit.
        guard role == kAXWindowRole else { return .otherDeclared }
        guard let subrole = declaration(subrole, original: observed?[kAXSubroleAttribute]) else { return .visualOnly }
        // A nonactivating NSPanel can itself declare AXSystemDialog. Do not
        // infer key eligibility from dialog names or impose a key requirement
        // on menus/panels. This strong gate covers standard documents only.
        return subrole == kAXStandardWindowSubrole ? .keyRequiring : .otherDeclared
    }

    static func ready(
        kind: NativePointerIntent.Kind, foreground: Bool, windowKind: WindowKind, focus: WindowFocus,
        initialFocus: WindowFocus? = nil, backgroundPrepared: Bool,
        backgroundReportedActive: Bool? = nil, requirement: Requirement = .contentReceiver,
        presentedMenuReceiver: @autoclosure () -> Bool = false
    ) -> Bool {
        // Foreground here already includes the exact original window hit.
        // A prior synthetic focus receipt cannot discharge this requirement.
        guard requirement != .foregroundWindowPoint || foreground else { return false }
        if foreground {
            if kind == .scroll { return true }
            switch windowKind {
            case .keyRequiring:
                switch focus {
                case .target: return true
                case .otherWindow: return false
                case .absent: return initialFocus?.reported != true
                // A provider failure or missing private bridge does not prove
                // a different key window. Keep the exact physical visual route.
                case .unresolved, .failed, .notRequired: return true
                }
            case .otherDeclared, .visualOnly: return true
            case .identityConflict: return false
            }
        }
        if !backgroundPrepared {
            // An already presented, exactly associated menu owns its tracking
            // receiver. Activating its document would cancel that receiver.
            return kind == .click && requirement == .contentReceiver && windowKind == .otherDeclared
                && presentedMenuReceiver()
        }
        switch windowKind {
        case .identityConflict: return false
        case .keyRequiring:
            // AXFocused belongs to the keyboard-focused element, which may be
            // a text field inside this key window. The application's
            // AXFocusedWindow establishes the window receiver instead.
            // A consumed prepared-point contract proves this original local
            // destination after owned foreground return. AX app activity is
            // independent of that window's ability to consume addressed input.
            return focus == .target
                && (backgroundReportedActive == true || requirement == .preparedLocalPoint)
        case .visualOnly: return kind == .scroll
        case .otherDeclared: return true
        }
    }
}

/// Object pointer contracts default to addressed transport. Explicit global
/// input has its own native primitive and never enters this window unit.
enum NativePointerDeliveryPolicy {
    static func delivery(_ selected: NativeClickDelivery?) -> NativeClickDelivery {
        selected ?? .windowDirected
    }
}

/// One selected native window route. No target search, foreground fallback,
/// screenshot refresh, AXPress substitution or post-dispatch retry lives here.
@MainActor struct NativePointerUnit {
    let application: NSRunningApplication
    let window: WindowGeometry.WindowMapping
    let nativeWindow: AXUIElement?
    private let observedWindowAttributes: [String: AXRead<CFTypeRef>]?
    private let capturedImage: NativeCapture.Entry?
    private let recipientElement: AXUIElement?
    let pointerRequirement: NativePointerReadiness.Requirement
    private var target: BackgroundTarget { BackgroundTarget(application) }

    init(
        application: NSRunningApplication, window: WindowGeometry.WindowMapping, nativeWindow: AXUIElement? = nil,
        observedWindowAttributes: [String: AXRead<CFTypeRef>]? = nil,
        capturedImage: NativeCapture.Entry? = nil,
        pointerRequirement: NativePointerReadiness.Requirement = .contentReceiver,
        recipientElement: AXUIElement? = nil
    ) {
        self.application = application
        self.window = window
        self.nativeWindow = nativeWindow
        self.observedWindowAttributes = observedWindowAttributes
        self.capturedImage = capturedImage
        self.pointerRequirement = pointerRequirement
        self.recipientElement = recipientElement
    }

    init(capture: NativeCapture.Input, pointerRequirement: NativePointerReadiness.Requirement = .contentReceiver) {
        self.init(
            application: capture.entry.surface.native?.application ?? capture.entry.surface.owner,
            window: capture.mapping, nativeWindow: capture.entry.surface.element,
            observedWindowAttributes: capture.entry.windowAttributes, capturedImage: capture.entry,
            pointerRequirement: pointerRequirement)
    }

    func requiring(_ requirement: NativePointerReadiness.Requirement) -> Self {
        Self(
            application: application, window: window, nativeWindow: nativeWindow,
            observedWindowAttributes: observedWindowAttributes, capturedImage: capturedImage,
            pointerRequirement: requirement, recipientElement: recipientElement)
    }

    /// QUERY: a new snapshot for an input that has not begun. The retained
    /// capture's pixel basis and native identity are never changed.
    func resolvingCaptureTranslation() throws -> Self {
        guard let capturedImage else { return self }
        let current = try capturedImage.screen.inputMapping(capturedImage.mapping)
        return Self(
            application: application, window: current, nativeWindow: nativeWindow,
            observedWindowAttributes: observedWindowAttributes, capturedImage: capturedImage,
            pointerRequirement: pointerRequirement, recipientElement: recipientElement)
    }

    func foregroundWindow(at point: CGPoint? = nil) -> Bool {
        target.isForeground && inputWindowHit(at: point).windowID == window.id
    }

    func inputWindowHit(at point: CGPoint? = nil) -> NativeInputWindowHit {
        BackgroundEvents.inputWindowHit(
            at: point ?? CGPoint(x: window.frame.midX, y: window.frame.midY), retained: window,
            nativeWindow: nativeWindow)
    }

    private var windowKind: NativePointerReadiness.WindowKind {
        // Standard documents need exact-window preparation before their first
        // button. Initial AX absence retains the foreground visual route.
        guard let nativeWindow else { return .visualOnly }
        var pid: pid_t = 0
        guard AXUIElementGetPid(nativeWindow, &pid) == .success else { return .visualOnly }
        guard pid == window.pid else { return .identityConflict }
        let role = AXAccess.read(nativeWindow, kAXRoleAttribute)
        let subrole = AXAccess.read(nativeWindow, kAXSubroleAttribute)
        return NativePointerReadiness.windowKind(
            role: role, subrole: subrole,
            nativeWindowMatches: NativePointerReadiness.nativeWindowMatches(
                reported: BackgroundEvents.nativeWindowID(nativeWindow), expected: window.id,
                presentedMenu: capturedMenuMappingIsCurrent),
            observed: observedWindowAttributes)
    }

    func validateWindowIdentity() throws {
        guard let nativeWindow else { return }
        var pid: pid_t = 0
        let owner = AXUIElementGetPid(nativeWindow, &pid)
        if (owner == .success && pid != window.pid)
            || NativePointerReadiness.nativeWindowMatches(
                reported: BackgroundEvents.nativeWindowID(nativeWindow), expected: window.id,
                presentedMenu: capturedMenuMappingIsCurrent) == false
        {
            throw DriverError(
                "the retained accessibility window no longer matches the pointer destination", code: .staleTarget)
        }
    }

    func foregroundPreparationPoint(at point: CGPoint) -> CGPoint? { windowKind == .keyRequiring ? nil : point }

    private func windowFocus() -> NativePointerReadiness.WindowFocus {
        // Timeout belongs only to this fresh root proxy, never the retained
        // target or another equal AX object used by observation/input.
        let root = AXUIElementCreateApplication(window.pid)
        AXUIElementSetMessagingTimeout(root, 0.1)
        switch AXAccess.read(root, kAXFocusedWindowAttribute) {
        case .unsupported, .unavailable(.noValue): return .absent
        case .value(let raw):
            guard CFGetTypeID(raw) == AXUIElementGetTypeID() else { return .failed }
            let focused = unsafeDowncast(raw, to: AXUIElement.self)
            var pid: pid_t = 0
            guard AXUIElementGetPid(focused, &pid) == .success, pid == window.pid else { return .unresolved }
            let equal = nativeWindow.map { CFEqual(focused, $0) } ?? false
            // Bound this new non-target proxy before even the optional native
            // bridge. Never change the retained target's messaging policy.
            if !equal { AXUIElementSetMessagingTimeout(focused, 0.05) }
            var id = BackgroundEvents.nativeWindowID(focused)
            if id == nil && !equal {
                id = NativeInputWindowQuery.publicIdentity(of: focused, pid: window.pid).window?.id
            }
            return NativePointerReadiness.focusIdentity(nativeID: id, retainedEqual: equal, targetID: window.id)
        default: return .failed
        }
    }

    /// Cell-editor activation checks whether the established foreground route
    /// can receive its preparation gesture. Ordinary background pointer
    /// readiness is determined independently by its dispatch pipeline.
    func canPrepareWindowDirected(kind: NativePointerIntent.Kind = .click) -> Bool {
        kind == .scroll || target.isForeground
    }

    /// The caller supplies an already-checked physical foreground/window hit.
    /// Reuse this for offer sampling, preparation and final event validation.
    private struct Readiness {
        let ready: Bool
        let foreground: Bool
        let focus: NativePointerReadiness.WindowFocus
        let windowKind: NativePointerReadiness.WindowKind
        var foregroundPID: pid_t? = nil
        var hit: NativeInputWindowHit? = nil
        var backgroundPrepared: Bool? = nil
        var backgroundReportedActive: Bool? = nil
    }

    private func contentReadiness(
        foreground: Bool, kind: NativePointerIntent.Kind, declaredWindow: NativePointerReadiness.WindowKind? = nil,
        initialFocus: NativePointerReadiness.WindowFocus? = nil,
        requirement: NativePointerReadiness.Requirement? = nil, at point: CGPoint? = nil
    ) -> Readiness {
        // The declaration class is gesture-local. Rechecking labels for every
        // down/up would add slow provider RPCs to the event cadence. The real
        // object/process/window remain checked throughout. A held capture drag
        // continues its original receiver instead of repeating initial hit tests.
        let declared = declaredWindow ?? (kind != .scroll ? windowKind : .visualOnly)
        let focus = declared == .keyRequiring ? windowFocus() : .notRequired
        let effectiveRequirement = requirement ?? pointerRequirement
        let prepared = !foreground && NativePointerReadiness.prepared(
            requirement: effectiveRequirement,
            cached: BackgroundEvents.pointerPreparationIsCurrent(target, window: window, needsKeyWindow: kind != .scroll))
        let active = !foreground && declared == .keyRequiring ? reportedActive() : nil
        return Readiness(
            ready: NativePointerReadiness.ready(
                kind: kind, foreground: foreground, windowKind: declared, focus: focus, initialFocus: initialFocus,
                backgroundPrepared: prepared,
                backgroundReportedActive: active, requirement: effectiveRequirement,
                presentedMenuReceiver: presentedMenu(at: point)),
            foreground: foreground, focus: focus, windowKind: declared,
            backgroundPrepared: foreground ? nil : prepared, backgroundReportedActive: active)
    }

    private func sampleReadiness(
        at point: CGPoint? = nil, kind: NativePointerIntent.Kind, delivery: NativeClickDelivery? = nil,
        requirement: NativePointerReadiness.Requirement? = nil
    ) -> Readiness {
        let selected = NativePointerDeliveryPolicy.delivery(delivery)
        let foregroundPID = NSWorkspace.shared.frontmostApplication?.processIdentifier
        let hit = foregroundPID == application.processIdentifier ? inputWindowHit(at: point) : nil
        let foreground = foregroundPID == application.processIdentifier && hit?.windowID == window.id
        var result: Readiness
        if selected != .windowDirected {
            result = Readiness(ready: false, foreground: foreground, focus: .notRequired, windowKind: .visualOnly)
        } else {
            result = contentReadiness(foreground: foreground, kind: kind, requirement: requirement, at: point)
        }
        result.foregroundPID = foregroundPID
        result.hit = hit
        return result
    }

    struct PhysicalClickSample {
        let ready: Bool
        let foreground: Bool
        let hit: NativeInputWindowHit?
        let windowKind: NativePointerReadiness.WindowKind
        let focus: NativePointerReadiness.WindowFocus

        init(
            ready: Bool, foreground: Bool, hit: NativeInputWindowHit?,
            windowKind: NativePointerReadiness.WindowKind = .visualOnly,
            focus: NativePointerReadiness.WindowFocus = .notRequired
        ) {
            self.ready = ready
            self.foreground = foreground
            self.hit = hit
            self.windowKind = windowKind
            self.focus = focus
        }
    }

    /// Preserve the already queried physical hit instead of reconstructing it
    /// from a ready boolean when a popup offer needs a typed point proof.
    func physicalClickSample(at point: CGPoint) -> PhysicalClickSample {
        let value = sampleReadiness(at: point, kind: .click)
        return .init(
            ready: value.ready && value.foreground, foreground: value.foreground, hit: value.hit,
            windowKind: value.windowKind,
            focus: value.focus)
    }

    private func diagnoseReadiness(_ value: Readiness, at point: CGPoint, phase: String) {
        guard WindowGeometry.inputDiagnosticsEnabled else { return }
        diagnose([
            "kind": "pointer_preparation_readiness", "phase": phase, "targetPID": application.processIdentifier,
            "window": window.id,
            "windowFrame": [window.frame.minX, window.frame.minY, window.frame.width, window.frame.height],
            "point": [point.x, point.y], "ready": value.ready, "foreground": value.foreground,
            "foregroundPID": value.foregroundPID as Any? ?? NSNull(),
            "windowKind": String(describing: value.windowKind), "windowFocus": String(describing: value.focus),
            "pointerRequirement": String(describing: pointerRequirement),
            "backgroundPrepared": value.backgroundPrepared as Any? ?? NSNull(),
            "backgroundReportedActive": value.backgroundReportedActive as Any? ?? NSNull(),
            "hitQueried": value.hit != nil, "hitWindow": value.hit?.windowID as Any? ?? NSNull(),
            "hitSource": value.hit?.source?.rawValue as Any? ?? NSNull(),
            "windowServerHit": value.hit.map { String(describing: $0.windowServer) } as Any? ?? NSNull(),
            "accessibilityHit": value.hit?.accessibility.map { String(describing: $0) } as Any? ?? NSNull()
        ])
    }

    /// This capture is already the exact live menu surface. Its tracker
    /// receives coordinates directly; it must not be re-prepared as a document.
    private var capturedMenuMappingIsCurrent: Bool {
        guard let capturedImage, let nativeWindow,
            capturedImage.surface.native?.menu != nil
                || observedWindowAttributes?[kAXRoleAttribute]?.value as? String == kAXMenuRole else { return false }
        return NativeMenuSurfaces.matches(nativeWindow, window: window)
    }

    private func presentedMenu(at point: CGPoint?) -> Bool {
        guard let point, window.frame.contains(point) else { return false }
        return capturedMenuMappingIsCurrent
    }

    func addressedPointerReceiver(
        at point: CGPoint,
        mechanismQualification: ((AXUIElement, String?) throws -> NativeAddressedPointerReceiver.Qualification?)? = nil
    ) throws -> NativeAddressedPointerReceiver.Qualification {
        // This unit already retains the original WindowServer identity and
        // capture mapping. Without a concrete AX child, a captured point may
        // still use addressed transport after exact foreground-window hit
        // preparation. It gains no semantic/background or element authority.
        guard let nativeWindow else { return .originalWindowPoint }
        try RequestDiagnostics.checkCancellation()
        if presentedMenu(at: point) { return .declaredInteraction }
        let sampled = NativeInputWindowQuery.pointHit(at: point, application: window.pid)
        let status = sampled.status
        try RequestDiagnostics.checkCancellation()
        guard status == .success, let hit = sampled.element, !CFEqual(hit, nativeWindow) else {
            diagnose([
                "kind": "pointer_receiver_evidence", "window": window.id, "queryStatus": status.rawValue,
                "nativeType": "AXUIElement", "supported": false,
                "qualification": NativeAddressedPointerReceiver.Qualification.originalWindowPoint.rawValue,
                "reason": "no concrete AX child; captured point retains exact foreground-window requirement"
            ])
            return .originalWindowPoint
        }
        let role = AXAccess.read(hit, kAXRoleAttribute).value as? String
        // Specialized receiver mechanisms consume this same fixed-point hit.
        // They may prove the original popup's forward ownership when Qt's
        // reverse AXWindow names its document, without another AX hit query.
        if let qualified = try mechanismQualification?(hit, role) { return qualified }
        var rawActions: CFArray?
        let actionStatus = AXUIElementCopyActionNames(hit, &rawActions)
        let actions = actionStatus == .success ? rawActions as? [String] : nil
        let evidence = NativeAddressedPointerReceiver.query(hit, role: role, actions: actions)
        let owner = WindowGeometry.owningWindow(hit, pid: window.pid, in: nativeWindow)
        let within = owner.map { CFEqual($0, nativeWindow) } == true
        let qualification = evidence.qualification(concreteHit: true, sameWindow: within)
        let supported = qualification == .declaredInteraction
        diagnose([
            "kind": "pointer_receiver_evidence", "window": window.id, "queryStatus": status.rawValue,
            "nativeType": "AXUIElement", "role": role as Any? ?? NSNull(), "actions": actions as Any? ?? NSNull(),
            "actionStatus": actionStatus.rawValue, "sameWindow": within, "supported": supported,
            "qualification": qualification.rawValue,
            "reason": supported ? "concrete control with advertised interaction"
                : qualification == .sameWindowPoint
                    ? "concrete hit in original window; captured point requires foreground window"
                    : "no addressed interaction capability"
        ])
        return qualification
    }

    func resolvedDelivery(_ selected: NativeClickDelivery?) -> NativeClickDelivery {
        NativePointerDeliveryPolicy.delivery(selected)
    }

    private func requireDirected(_ selected: NativeClickDelivery) throws {
        guard selected == .windowDirected else {
            throw DriverError("window pointer input requires directed delivery", code: .invalidRequest)
        }
    }

    private func reportedActive() -> Bool? {
        let root = AXUIElementCreateApplication(window.pid)
        AXUIElementSetMessagingTimeout(root, 0.1)
        return AXAccess.read(root, kAXFrontmostAttribute).value as? Bool
    }

    func backgroundPreparationAvailable(kind: NativePointerIntent.Kind = .click) -> Bool {
        guard pointerRequirement != .foregroundWindowPoint else { return false }
        if target.isForeground || kind == .scroll { return true }
        switch windowKind {
        case .keyRequiring, .otherDeclared:
            // A live internal receiver can be prepared without changing the
            // system foreground. Dispatch rechecks that receiver independently
            // of the synthetic transport-preparation record.
            return true
        case .visualOnly, .identityConflict: return false
        }
    }

    func ready(at point: CGPoint? = nil, kind: NativePointerIntent.Kind = .click, delivery: NativeClickDelivery? = nil)
        -> Bool
    {
        let readiness = sampleReadiness(at: point, kind: kind, delivery: delivery)
        if let point { diagnoseReadiness(readiness, at: point, phase: "selectedClickPreparation") }
        return readiness.ready
    }

    enum WindowPreparationOutcome: Equatable { case alreadyReady, contextPrepared }

    /// Prepare only the retained window context. An element caller must refresh
    /// its geometry before testing a point after this potentially moving phase.
    /// A context receipt never authorizes pointer delivery on its own.
    func prepareWindowContext(
        foreground: Bool, atNative point: CGPoint, kind: NativePointerIntent.Kind = .click,
        delivery: NativeClickDelivery? = nil, check: @escaping @MainActor () throws -> Void,
        onPreparation: @escaping @MainActor (String) -> Void, onCleanupUnknown: @escaping @MainActor () -> Void
    ) async throws -> WindowPreparationOutcome {
        try check()
        try target.validate(input: true)
        try validateWindowIdentity()
        let selected = resolvedDelivery(delivery)
        try requireDirected(selected)
        let initial = sampleReadiness(at: point, kind: kind, delivery: selected)
        diagnoseReadiness(initial, at: point, phase: "beforePreparation")
        try check()
        if initial.ready { return .alreadyReady }
        if foreground {
            let assist = ForegroundAssist()
            // The point alone cannot prepare a different standard document:
            // the first mouse-down may only make it key. Keep hit-only
            // preparation for non-key panels, menus and visual-only surfaces.
            try await assist.prepare(
                target, window: window.id, pointerAt: kind != .scroll && windowKind == .keyRequiring ? nil : point,
                reusePointerAt: kind == .click ? point : nil,
                onAction: { onPreparation("foreground window activation") }, onCleanupUnknown: onCleanupUnknown)
        } else {
            guard backgroundPreparationAvailable(kind: kind) else {
                throw NativePreparationUnavailable(
                    description: "the native content receiver needs foreground preparation; no pointer event sent")
            }
            do {
                try await NativeFocusPipeline.prepare(
                    target, window: window, nativeWindow: nativeWindow, needsKeyWindow: kind != .scroll,
                    onPreparation: onPreparation, onCleanupUnknown: onCleanupUnknown, beforeAction: check)
            } catch let error as DriverError where error.code == .inputUnavailable {
                try check()
                try target.validate(input: true)
                throw NativePreparationUnavailable(description: error.description)
            }
        }
        try check()
        return .contextPrepared
    }

    /// QUERY: validate the caller's current point, without another activation,
    /// input event or preparation attempt.
    func requirePointReadiness(
        atNative point: CGPoint, kind: NativePointerIntent.Kind = .click, delivery: NativeClickDelivery? = nil
    ) throws {
        let selected = resolvedDelivery(delivery)
        let prepared = sampleReadiness(at: point, kind: kind, delivery: selected)
        diagnoseReadiness(prepared, at: point, phase: "afterPreparation")
        guard prepared.ready else {
            throw NativePreparationUnavailable(description: "pointer route is not ready; no pointer event sent")
        }
    }

    /// Coordinate input keeps its original point and preparation/query order.
    func prepare(
        foreground: Bool, atNative point: CGPoint, kind: NativePointerIntent.Kind = .click,
        delivery: NativeClickDelivery? = nil, check: @escaping @MainActor () throws -> Void,
        onPreparation: @escaping @MainActor (String) -> Void, onCleanupUnknown: @escaping @MainActor () -> Void
    ) async throws {
        let selected = resolvedDelivery(delivery)
        let outcome = try await prepareWindowContext(
            foreground: foreground, atNative: point, kind: kind, delivery: selected, check: check,
            onPreparation: onPreparation, onCleanupUnknown: onCleanupUnknown)
        if outcome == .contextPrepared { try requirePointReadiness(atNative: point, kind: kind, delivery: selected) }
    }

    func point(_ pixel: CGPoint) throws -> CGPoint {
        try NativeCapturedPointerPlan(mapping: window).point(pixel)
    }

    func prepareCaptured(
        _ request: NativePointerIntent, foreground: Bool, check: @escaping @MainActor () throws -> Void,
        onPreparation: @escaping @MainActor (String) -> Void, onCleanupUnknown: @escaping @MainActor () -> Void
    ) async throws {
        try check()
        let current = try resolvingCaptureTranslation()
        try current.validateParameters(request)
        let point = try current.point(request.start!)
        let delivery = current.resolvedDelivery(nil)
        let outcome = try await current.prepareWindowContext(
            foreground: foreground, atNative: point, kind: request.kind, delivery: delivery,
            check: check, onPreparation: onPreparation, onCleanupUnknown: onCleanupUnknown)
        if outcome == .contextPrepared {
            let prepared = try current.resolvingCaptureTranslation()
            try prepared.requirePointReadiness(
                atNative: prepared.point(request.start!), kind: request.kind, delivery: delivery)
        }
    }

    func validateParameters(_ request: NativePointerIntent) throws {
        guard let start = request.start else {
            throw DriverError("window image input requires image coordinates", code: .invalidRequest)
        }
        _ = try point(start)
        if let end = request.end { _ = try point(end) }
        if let pages = request.pages {
            let size = ["up", "down"].contains(request.direction!) ? window.frame.height : window.frame.width
            guard pages * size < Double(Int32.max) else {
                throw DriverError("scroll amount exceeds native event range", code: .invalidRequest)
            }
        }
    }

    /// Foreground is needed to expose this original captured point, not to
    /// keep a standard document active for its whole click. Fix the local
    /// recipient first, return foreground before an effect can open a menu,
    /// then prove the existing background window mechanism from fresh facts.
    func prepareBackgroundPoint(
        _ request: NativePointerIntent, check: @escaping @MainActor () throws -> Void,
        onPreparation: @escaping @MainActor (String) -> Void,
        onCleanupUnknown: @escaping @MainActor () -> Void,
        onNativePreparation: @escaping @MainActor (NativePreparationEffect) -> Void
    ) async throws -> NativePreparedPointerPoint? {
        guard request.kind == .click, capturedImage != nil,
            let responsibility = NativeForegroundResponsibility.current,
            responsibility.hasForegroundObligation, let original = responsibility.original,
            original.pid != window.pid, windowKind == .keyRequiring,
            let process = target.capturedProcessIdentity
        else { return nil }
        try check()
        let current = try resolvingCaptureTranslation()
        let point = try current.point(request.start!)
        let initial = current.sampleReadiness(at: point, kind: .click)
        guard initial.ready, initial.foreground else { return nil }
        let recipient = try NativePointerRecipient.resolve(
            current.window, nativeWindow: nativeWindow, element: recipientElement, at: point,
            foregroundHit: initial.hit)
        guard recipient.canPrepareBackgroundPoint else { return nil }
        return try await current.handoffBackgroundPoint(
            point, recipient: recipient, responsibility: responsibility, process: process,
            check: check, onPreparation: onPreparation, onCleanupUnknown: onCleanupUnknown,
            onNativePreparation: onNativePreparation)
    }

    /// Reused before dispatch and before the first requested down. Entry/move
    /// may provoke delayed activation; the recipient remains fixed throughout.
    private func handoffBackgroundPoint(
        _ point: CGPoint, recipient: NativePointerRecipient, responsibility: NativeForegroundResponsibility,
        process: NativeProcessIdentity, check: @escaping @MainActor () throws -> Void,
        onPreparation: @escaping @MainActor (String) -> Void,
        onCleanupUnknown: @escaping @MainActor () -> Void,
        onNativePreparation: @escaping @MainActor (NativePreparationEffect) -> Void
    ) async throws -> NativePreparedPointerPoint? {
        let current = self
        let ownership = responsibility.state(target: process)
        guard responsibility.hasForegroundObligation, !ownership.externalActivation,
            ownership.foregroundPID == window.pid, responsibility.permitsForegroundPreparation(),
            !hasKnownTransient(at: point)
        else { return nil }
        try check()
        responsibility.releaseForegroundForAddressedInput()
        // This point already has a fixed local recipient and an established
        // standard window. Returning foreground is the remaining preparation;
        // a new activity notification would undo that return in some providers.
        try await NativeForegroundRestorationScope.perform(
            target: target, onPreparation: onPreparation, onCleanupUnknown: onCleanupUnknown
        ) { try check() }
        try check()
        _ = try recipient.validate(host: current.window)
        let after = current.sampleReadiness(at: point, kind: .click, requirement: .preparedLocalPoint)
        current.diagnoseReadiness(after, at: point, phase: "handoffAfterReturn")
        if !after.foreground, after.ready {
            diagnose(["kind": "prepared_pointer_point", "phase": "readyAfterForegroundReturn",
                      "window": current.window.id, "recipientPID": recipient.eventWindow.pid,
                      "point": [point.x, point.y], "requestedMouseDownAttempted": false])
            return NativePreparedPointerPoint(window: current.window, point: point, recipient: recipient, process: process)
        }
        // No requested mouse-down has been sent. A missing local mechanism removes
        // only this optional handoff; retain the existing foreground recipe.
        // ForegroundAssist still honors user takeover and the original budget.
        try await ForegroundAssist().prepare(
            target, window: current.window.id, pointerAt: current.foregroundPreparationPoint(at: point),
            onAction: { onPreparation("foreground window activation after unavailable local handoff") },
            onCleanupUnknown: onCleanupUnknown,
            beforeAction: {
                try check()
                guard responsibility.permitsForegroundPreparation() else {
                    throw NativePreparationUnavailable(
                        description: "user takeover ended foreground handoff recovery; no repeated activation sent")
                }
            })
        return nil
    }

    /// This only excludes optional foreground restitution; it never rejects
    /// pointer input. A known menu/expanded control keeps its current context.
    private func hasKnownTransient(at point: CGPoint) -> Bool {
        var element = recipientElement ?? NativeInputWindowQuery.pointHit(at: point, application: window.pid).element
        var seen = [AXUIElement]()
        while let current = element, seen.count < 12, !seen.contains(where: { CFEqual($0, current) }) {
            if let nativeWindow, CFEqual(current, nativeWindow) { return false }
            seen.append(current)
            let role = AXAccess.read(current, kAXRoleAttribute).value as? String
            if [kAXMenuRole, kAXMenuItemRole, kAXMenuBarItemRole, kAXMenuButtonRole,
                kAXPopUpButtonRole, kAXComboBoxRole, "AXPopover", kAXSheetRole].contains(role ?? "")
                || AXAccess.read(current, kAXExpandedAttribute).value as? Bool == true { return true }
            element = AXAccess.element(current, kAXParentAttribute)
        }
        return false
    }

    func deliver(
        _ request: NativePointerIntent, check: @escaping @MainActor () throws -> Void,
        guardOperation: @escaping @MainActor () throws -> Void, onDispatch: () throws -> Void,
        onPosted: () -> Void, onCleanupUnknown: @escaping @MainActor () -> Void, delivery: NativeClickDelivery? = nil,
        onPreparation: @escaping @MainActor (String) -> Void = { _ in },
        onNativePreparation: @escaping @MainActor (NativePreparationEffect) -> Void = { _ in },
        preparedPoint: NativePreparedPointerPoint? = nil
    ) async throws {
        try check()
        let current = try resolvingCaptureTranslation()
        try current.validateParameters(request)
        let sampleGeometry: @MainActor (Bool) throws -> WindowGeometry.WindowMapping = { continuingDrag in
            guard let image = current.capturedImage else { return current.window }
            return try image.screen.validateInputGeometry(current.window, continuingDrag: continuingDrag)
        }
        try await current.deliver(
            request, from: current.point(request.start!), to: request.end.map(current.point),
            viewport: current.window.frame.size, check: check, guardOperation: guardOperation,
            captureGeometry: sampleGeometry,
            onDispatch: onDispatch, onPosted: onPosted, onCleanupUnknown: onCleanupUnknown, delivery: delivery,
            onPreparation: onPreparation, onNativePreparation: onNativePreparation, preparedPoint: preparedPoint)
    }

    /// Geometry has one owner: an immutable image transform or a freshly
    /// resolved Element. Both use this exact event route and paired cleanup.
    func deliver(
        _ request: NativePointerIntent, from start: CGPoint, to end: CGPoint?, viewport: CGSize,
        check: @escaping @MainActor () throws -> Void,
        guardOperation: @escaping @MainActor () throws -> Void,
        captureGeometry: (@MainActor (Bool) throws -> WindowGeometry.WindowMapping)? = nil,
        onDispatch: () throws -> Void, onPosted: () -> Void,
        onCleanupUnknown: @escaping @MainActor () -> Void, delivery: NativeClickDelivery? = nil,
        onPreparation: @escaping @MainActor (String) -> Void = { _ in },
        onNativePreparation: @escaping @MainActor (NativePreparationEffect) -> Void = { _ in },
        preparedPoint: NativePreparedPointerPoint? = nil, confirmedService: NativePointerRecipient? = nil,
        continueSecondClick: ((CGPoint) throws -> WindowGeometry.WindowMapping?)? = nil
    ) async throws {
        try check()
        try target.validate(input: true)
        try validateWindowIdentity()
        let selected = resolvedDelivery(delivery)
        try requireDirected(selected)
        let preparedRecipient = try preparedPoint?.consume(window: window, point: start)
        var requirement = preparedRecipient == nil ? pointerRequirement : .preparedLocalPoint
        let initial = sampleReadiness(at: start, kind: request.kind, delivery: selected, requirement: requirement)
        try check()
        guard initial.ready else { throw DriverError("pointer route changed before dispatch", code: .inputUnavailable) }
        // The object gesture remains addressed after activation. Actual global
        // input has no window unit, receiver selection or object boundary.
        let source = try Input.source()
        let capturedDrag = capturedImage != nil && request.kind == .drag
        // Only event addressing follows the live window origin. The sequence
        // below was built from the one frozen screen start/end and viewport.
        var hostEventWindow = window
        try BackgroundEvents.requirePointerRoute(window: window)
        guard let process = NativeProcessIdentity.read(window.pid) else {
            throw DriverError("pointer recipient disappeared", code: .staleTarget)
        }
        let recipient = try confirmedService ?? preparedRecipient ?? NativePointerRecipient.resolve(
            window, nativeWindow: nativeWindow, element: recipientElement, at: start,
            foregroundHit: initial.foreground ? initial.hit : nil)
        var eventWindow = try confirmedService?.validate(host: window) ?? recipient.eventWindow
        let sequence = try NativePointerEvents(
            request: request, from: start, to: end, viewport: viewport, source: source)
        guard
            let entry = NSEvent.enterExitEvent(
                with: .mouseEntered,
                location: NSPoint(x: start.x - eventWindow.frame.minX, y: eventWindow.frame.maxY - start.y),
                modifierFlags: [], timestamp: ProcessInfo.processInfo.systemUptime, windowNumber: Int(eventWindow.id),
                context: nil, eventNumber: 0, trackingNumber: 0, userData: nil)?.cgEvent
        else { throw DriverError("cannot allocate pointer entry event", code: .inputUnavailable) }
        func post(_ event: CGEvent, point: CGPoint) {
            AgentPointer.current?.move(
                to: point, window: hostEventWindow,
                pulse: request.kind == .click
                    && [.leftMouseDown, .rightMouseDown, .otherMouseDown].contains(event.type), count: request.count,
                dragging: request.kind == .drag ? event.type != .leftMouseUp : nil)
            event.location = point
            BackgroundEvents.post(
                event, window: eventWindow, point: point, clickGroup: sequence.group, presenterPID: window.pid)
        }
        func queryRecipient(continuingDrag: Bool) throws {
            try target.validate(input: true)
            guard NativeProcessIdentity.read(window.pid) == process else {
                throw DriverError("pointer recipient changed", code: .staleTarget)
            }
            if let captureGeometry { hostEventWindow = try captureGeometry(continuingDrag) }
            eventWindow = try recipient.validate(host: hostEventWindow)
        }
        func validate(_ point: CGPoint, held: Bool) throws {
            let continuingDrag = capturedDrag && held
            var readiness: Readiness?
            try ValidateNativePointerEventPass.run(
                kind: request.kind, continuingDrag: continuingDrag,
                guardOperation: guardOperation, checkTarget: check,
                queryRecipient: { try queryRecipient(continuingDrag: continuingDrag) }, queryReadiness: {
                    let current = contentReadiness(
                        foreground: foregroundWindow(at: point), kind: request.kind,
                        declaredWindow: initial.windowKind, initialFocus: initial.focus, requirement: requirement, at: point)
                    readiness = current
                    return current.ready
                }, checkPhysicalInput: {}, unavailable: {
                    if let current = readiness, let missing = NativePointerActivityRecovery.missing(
                        window: window.id, kind: request.kind, delivery: selected,
                        requirement: requirement, remote: recipient.isRemote,
                        initialReady: initial.ready, initialFocus: initial.focus,
                        initialForeground: initial.foreground, initialPrepared: initial.backgroundPrepared,
                        currentReady: current.ready, currentForeground: current.foreground,
                        windowKind: current.windowKind, currentFocus: current.focus,
                        currentActive: current.backgroundReportedActive)
                    { return missing }
                    return DriverError("background pointer route changed", code: .inputUnavailable)
                })
        }
        func restoreLocalActivity(_ point: CGPoint, _ missing: NativePointerActivityRecovery.Missing) async throws {
            guard missing.window == window.id else { throw missing }
            let checkOriginal: @MainActor () throws -> Void = {
                try guardOperation()
                try queryRecipient(continuingDrag: false)
                try check()
            }
            try checkOriginal()
            diagnose(["kind": "pointer_activity_recovery", "phase": "prepare", "window": window.id,
                      "requestedMouseDownAttempted": false, "replayedPointerEvents": 0,
                      "selectedDelivery": "windowDirected", "reason": "knownLocalActivityLost"])
            var outcome = "failed"
            defer {
                diagnose(["kind": "pointer_activity_recovery", "phase": "completed", "window": window.id,
                          "outcome": outcome, "requestedMouseDownAttempted": false,
                          "replayedPointerEvents": 0,
                          "foregroundPID": NSWorkspace.shared.frontmostApplication?.processIdentifier as Any? ?? NSNull()])
            }
            let active = try await NativeApplicationContextUnit(application: application).prepare(
                requirement: .activeWhenKnownInactive, check: checkOriginal,
                onPreparation: onPreparation, onCleanupUnknown: { onCleanupUnknown() },
                onNativePreparation: { event in
                    onNativePreparation(.init(
                        kind: event.kind, phase: event.phase,
                        detail: "beforeFirstMouseDown; requestedMouseDownAttempted=false; replayedPointerEvents=0; " + event.detail,
                        window: window.id))
                })
            try checkOriginal()
            // No window selection/raise is permitted here. A local activation
            // must restore this same receiver itself; another document or an
            // absent/unknown key window stops the still-unpressed gesture.
            guard active == .activityObserved, !target.isForeground,
                reportedActive() == true, windowFocus() == .target
            else { throw missing }
            try BackgroundEvents.requirePointerRoute(window: window)
            try SyntheticFocusCoordinator.shared.remember(target, window: window, needsKeyWindow: true)
            try checkOriginal()
            outcome = "localActivityAndOriginalReceiverConfirmed"
        }
        func canRelease() -> Bool {
            if capturedDrag, let captureGeometry,
                let current = try? captureGeometry(true)
            {
                // The last drag may itself have moved the window. This cleanup
                // query cannot adopt a different window/owner. If it is gone,
                // retain the last confirmed local origin and original route;
                // ordinary scope/readiness failures must not strand the up.
                hostEventWindow = current
                eventWindow = recipient.releaseWindow(host: current)
            }
            if recipient.isRemote { return recipient.canRelease() }
            return NativeProcessIdentity.read(window.pid) == process
        }
        func handoffBeforeFirstDown(_ point: CGPoint) async throws {
            // A foreground preparation may settle after the entry/move. This
            // optional, once-only phase precedes the requested down, not its
            // effect. It never replays entry/move or retargets the gesture.
            guard request.kind == .click, preparedRecipient == nil, !initial.foreground,
                initial.windowKind == .keyRequiring, hostEventWindow.frame == window.frame,
                recipient.canPrepareBackgroundPoint, !recipient.isRemote,
                let responsibility = NativeForegroundResponsibility.current,
                let original = responsibility.original, original.pid != window.pid
            else { return }
            try guardOperation()
            try queryRecipient(continuingDrag: false)
            try check()
            let current = sampleReadiness(at: point, kind: .click, requirement: requirement)
            guard current.foreground, current.ready else { return }
            let proof = try await NativeInputActivity.$stage.withValue(.preparation) {
                try await handoffBackgroundPoint(
                    point, recipient: recipient, responsibility: responsibility, process: process,
                    check: {
                        try guardOperation()
                        try queryRecipient(continuingDrag: false)
                        try check()
                    }, onPreparation: onPreparation, onCleanupUnknown: onCleanupUnknown,
                    onNativePreparation: onNativePreparation)
            }
            if let proof {
                _ = try proof.consume(window: window, point: point)
                requirement = .preparedLocalPoint
                diagnose(["kind": "prepared_pointer_point", "phase": "beforeFirstMouseDown",
                          "window": window.id, "recipientPID": recipient.eventWindow.pid,
                          "requestedMouseDownAttempted": false, "replayedPointerEvents": 0])
            }
        }
        func validateSecondDown(_ point: CGPoint) throws -> Bool {
            guard let continueSecondClick, !recipient.isRemote, point == start else { return false }
            try guardOperation()
            try queryRecipient(continuingDrag: false)
            guard let child = try continueSecondClick(point) else { return false }
            guard child.id != window.id, child.pid == window.pid, child.frame.contains(point),
                NativeProcessIdentity.read(child.pid) == process
            else { throw DriverError("the cell editor pointer endpoint changed", code: .staleTarget) }
            try BackgroundEvents.requirePointerRoute(window: child)
            try guardOperation()
            // After the first up, the second down and its cleanup can keep
            // this one proved endpoint without a new move or gesture.
            eventWindow = child
            return true
        }
        let secondDown: ((CGPoint) throws -> Bool)?
        if continueSecondClick != nil { secondDown = { try validateSecondDown($0) } }
        else { secondDown = nil }
        try await sequence.deliver(
            entry: entry, check: validate, post: post,
            canRelease: canRelease, skipped: onCleanupUnknown,
            onDispatch: onDispatch, onPosted: onPosted, recoverLocalActivity: restoreLocalActivity,
            beforeFirstMouseDown: handoffBeforeFirstDown,
            validateSecondDown: secondDown)
    }
}

/// Event construction and bounded delivery. The chosen route supplies posting
/// and live checks; tests exercise the same sequence without posting OS input.
@MainActor struct NativePointerEvents {
    struct Item {
        let event: CGEvent?
        let release: CGEvent?
        let point: CGPoint
        let delayMs: Int
    }
    let items: [Item]
    let group: Int64
    private let source: CGEventSource
    private let settleUnpaired: Bool
    private let isClick: Bool

    init(request: NativePointerIntent, from: CGPoint, to: CGPoint?, viewport: CGSize, source: CGEventSource) throws {
        let points = [from] + (to.map { [$0] } ?? [])
        guard points.allSatisfy({ $0.x.isFinite && $0.y.isFinite }), viewport.width.isFinite, viewport.height.isFinite,
            viewport.width > 0, viewport.height > 0
        else { throw DriverError("invalid native pointer geometry", code: .invalidRequest) }
        self.source = source
        settleUnpaired = request.kind == .scroll
        isClick = request.kind == .click
        // CGEvent's mouse event number is represented as a signed 16-bit
        // NSEvent number; larger values are silently truncated by CoreGraphics.
        let group = Int64.random(in: 1...Int64(Int16.max))
        self.group = group
        let button: CGMouseButton = request.button == "right" ? .right : request.button == "middle" ? .center : .left
        let down: CGEventType =
            request.button == "right" ? .rightMouseDown : request.button == "middle" ? .otherMouseDown : .leftMouseDown
        let up: CGEventType =
            request.button == "right" ? .rightMouseUp : request.button == "middle" ? .otherMouseUp : .leftMouseUp
        func mouse(_ type: CGEventType, at point: CGPoint, count: Int = 1) throws -> CGEvent {
            guard
                let event = CGEvent(
                    mouseEventSource: source, mouseType: type, mouseCursorPosition: point, mouseButton: button)
            else { throw DriverError("cannot allocate pointer event", code: .inputUnavailable) }
            event.flags = []
            // Down/up and the initial positioning event carry no relative
            // motion. Do not inherit a previous event source's delta.
            event.setIntegerValueField(.mouseEventDeltaX, value: 0)
            event.setIntegerValueField(.mouseEventDeltaY, value: 0)
            event.setIntegerValueField(.mouseEventClickState, value: Int64(count))
            event.setIntegerValueField(.mouseEventNumber, value: group)
            event.setDoubleValueField(.mouseEventPressure, value: type == down || type == .leftMouseDragged ? 1 : 0)
            return event
        }
        var result = [Item(event: try mouse(.mouseMoved, at: from), release: nil, point: from, delayMs: 0)]
        switch request.kind {
        case .click:
            if request.screen {
                // The first HID move can queue an AppKit tracking area's
                // enter. Send one more move at that same point after the
                // queued enter has had time to run, before any button down.
                result.append(.init(event: try mouse(.mouseMoved, at: from), release: nil, point: from, delayMs: 15))
            }
            for count in 1...request.count {
                result.append(
                    .init(
                        event: try mouse(down, at: from, count: count), release: try mouse(up, at: from, count: count),
                        point: from, delayMs: count == 1 ? 15 : 45))
                result.append(.init(event: nil, release: nil, point: from, delayMs: 25))
            }
        case .scroll:
            let vertical = ["up", "down"].contains(request.direction!)
            let distance = request.pages! * (vertical ? viewport.height : viewport.width)
            guard distance.isFinite, distance > 0, distance < Double(Int32.max) else {
                throw DriverError("invalid native scroll distance", code: .invalidRequest)
            }
            let delta = Int32(max(1, distance.rounded())) * (["down", "right"].contains(request.direction!) ? -1 : 1)
            guard
                let event = CGEvent(
                    scrollWheelEvent2Source: source, units: .pixel, wheelCount: 2, wheel1: vertical ? delta : 0,
                    wheel2: vertical ? 0 : delta, wheel3: 0)
            else { throw DriverError("cannot allocate scroll event", code: .inputUnavailable) }
            event.flags = []
            event.location = from
            result.append(.init(event: event, release: nil, point: from, delayMs: 15))
        case .drag:
            guard let to else { throw DriverError("drag endpoint missing", code: .invalidRequest) }
            result.append(
                .init(event: try mouse(down, at: from), release: try mouse(up, at: from), point: from, delayMs: 15))
            // Establish tracking at the press location before any displacement.
            // A first moved sample can both initiate native window tracking and
            // be consumed as movement, duplicating that first displacement.
            result.append(.init(event: try mouse(.leftMouseDragged, at: from), release: nil, point: from, delayMs: 12))
            let count = max(8, Int(min(60, ceil(hypot(to.x - from.x, to.y - from.y) / 8))))
            var previous = from
            for step in 1...count {
                let fraction = Double(step) / Double(count)
                let point = CGPoint(x: from.x + (to.x - from.x) * fraction, y: from.y + (to.y - from.y) * fraction)
                // Check before converting to native integer deltas. Real window
                // geometry is much smaller; malformed input must never trap.
                // Quantize cumulative displacement, then take the difference.
                // Rounding each small segment independently accumulates drift
                // in consumers that use deltas (including titlebar dragging).
                let dx = (point.x - from.x).rounded() - (previous.x - from.x).rounded()
                let dy = (point.y - from.y).rounded() - (previous.y - from.y).rounded()
                guard dx.isFinite, dy.isFinite, abs(dx) < Double(Int64.max), abs(dy) < Double(Int64.max) else {
                    throw DriverError("drag delta exceeds native event range", code: .invalidRequest)
                }
                let event = try mouse(.leftMouseDragged, at: point)
                event.setIntegerValueField(.mouseEventDeltaX, value: Int64(dx))
                event.setIntegerValueField(.mouseEventDeltaY, value: Int64(dy))
                result.append(.init(event: event, release: nil, point: point, delayMs: 12))
                previous = point
            }
            result.append(.init(event: nil, release: nil, point: to, delayMs: 12))
        }
        items = result
    }

    func deliver(
        entry: CGEvent? = nil, check: (CGPoint, Bool) throws -> Void, post: @escaping (CGEvent, CGPoint) -> Void,
        canRelease: @escaping () -> Bool, skipped: @escaping () -> Void, onDispatch: () throws -> Void,
        onPosted: () -> Void,
        recoverLocalActivity: ((CGPoint, NativePointerActivityRecovery.Missing) async throws -> Void)? = nil,
        beforeFirstMouseDown: ((CGPoint) async throws -> Void)? = nil,
        validateSecondDown: ((CGPoint) throws -> Bool)? = nil
    ) async throws {
        var lastPoint = items[0].point
        var releaseMetric: NativeMetrics.Interval?
        var requestedMouseDownAttempted = false
        var localActivityRecoveryAttempted = false
        // Time the existing release eligibility + post/skip path. Its bounded
        // settling interval remains included in the enclosing clickDispatch.
        func endReleaseMetric() {
            releaseMetric?.end()
            releaseMetric = nil
        }
        let pair = PairedInput<CGEvent>(
            source: source,
            post: {
                defer { endReleaseMetric() }
                post($0, lastPoint)
            },
            canRelease: {
                releaseMetric = isClick ? RequestDiagnostics.metrics?.start(.clickRelease) : nil
                return canRelease()
            },
            skipped: {
                defer { endReleaseMetric() }
                skipped()
            }, settleUnpaired: settleUnpaired)
        func validate(_ point: CGPoint, _ held: Bool) throws {
            let metric = isClick ? RequestDiagnostics.metrics?.start(.clickTargetValidation) : nil
            defer { metric?.end() }
            try check(point, held)
        }
        func recover(_ missing: NativePointerActivityRecovery.Missing, at point: CGPoint, held: Bool) async throws {
            guard isClick, !held, !requestedMouseDownAttempted, !localActivityRecoveryAttempted,
                let recoverLocalActivity
            else { throw missing }
            // Consume once before awaiting: no requested-down outcome, failed
            // preparation, or later click may reopen this allowance.
            localActivityRecoveryAttempted = true
            try await recoverLocalActivity(point, missing)
        }
        try await pair.run {
            var held = false
            var completedClickPairs = 0
            if let entry {
                do { try validate(lastPoint, false) }
                catch let missing as NativePointerActivityRecovery.Missing {
                    try await recover(missing, at: lastPoint, held: false)
                    try validate(lastPoint, false)
                }
                try onDispatch()
                post(entry, lastPoint)
                onPosted()
            }
            for item in items {
                if item.delayMs > 0 { try await Task.sleep(for: .milliseconds(item.delayMs)) }
                try Task.checkCancellation()
                // Mouse-up belongs to the original event route, even when the
                // down closed its window. Do not require post-action UI state.
                guard let event = item.event else {
                    try pair.release()
                    if isClick && held { completedClickPairs += 1 }
                    held = false
                    continue
                }
                if isClick, !requestedMouseDownAttempted,
                    [.leftMouseDown, .rightMouseDown, .otherMouseDown].contains(event.type) {
                    try await beforeFirstMouseDown?(item.point)
                }
                let continued = try completedClickPairs == 1 && item.release != nil
                    && event.getIntegerValueField(.mouseEventClickState) == 2
                    && validateSecondDown?(item.point) == true
                if !continued {
                    do { try validate(item.point, held) }
                    catch let missing as NativePointerActivityRecovery.Missing {
                        try await recover(missing, at: item.point, held: held)
                        try validate(item.point, held)
                    }
                }
                lastPoint = item.point
                if [.leftMouseDown, .rightMouseDown, .otherMouseDown].contains(event.type) {
                    requestedMouseDownAttempted = true
                }
                try onDispatch()
                if let release = item.release {
                    try pair.press(event, release: release)
                    held = true
                } else {
                    post(event, lastPoint)
                }
                onPosted()
            }
        }
    }
}
