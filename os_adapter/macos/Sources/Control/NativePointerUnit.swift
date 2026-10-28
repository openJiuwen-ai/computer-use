// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices

/// Window-directed transport can address background content without global
/// activation. Readiness permits one delivery; its receipt leaves the actual
/// content effect unconfirmed until a subsequent observation.
enum NativePointerReadiness {
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
        initialFocus: WindowFocus? = nil, backgroundPrepared: Bool, exclusive: Bool
    ) -> Bool {
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
        return !exclusive && backgroundPrepared && windowKind != .identityConflict
    }
}

/// One selected native window route. No target search, foreground fallback,
/// screenshot refresh, AXPress substitution or post-dispatch retry lives here.
@MainActor struct NativePointerUnit {
    let application: NSRunningApplication
    let window: WindowGeometry.WindowMapping
    let nativeWindow: AXUIElement?
    private let observedWindowAttributes: [String: AXRead<CFTypeRef>]?
    private var target: BackgroundTarget { BackgroundTarget(application) }

    init(
        application: NSRunningApplication, window: WindowGeometry.WindowMapping, nativeWindow: AXUIElement? = nil,
        observedWindowAttributes: [String: AXRead<CFTypeRef>]? = nil
    ) {
        self.application = application
        self.window = window
        self.nativeWindow = nativeWindow
        self.observedWindowAttributes = observedWindowAttributes
    }

    init(capture: NativeCapture.Entry) {
        self.init(
            application: capture.surface.native?.application ?? capture.surface.owner, window: capture.mapping,
            nativeWindow: capture.surface.element, observedWindowAttributes: capture.windowAttributes)
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
            nativeWindowMatches: BackgroundEvents.nativeWindowID(nativeWindow).map { $0 == window.id },
            observed: observedWindowAttributes)
    }

    func validateWindowIdentity() throws {
        guard let nativeWindow else { return }
        var pid: pid_t = 0
        let owner = AXUIElementGetPid(nativeWindow, &pid)
        if (owner == .success && pid != window.pid)
            || BackgroundEvents.nativeWindowID(nativeWindow).map({ $0 != window.id }) == true
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

    /// The caller supplies an already-checked physical foreground/window hit.
    /// Reuse this for offer sampling, preparation and final event validation.
    private struct Readiness {
        let ready: Bool
        let foreground: Bool
        let focus: NativePointerReadiness.WindowFocus
        let windowKind: NativePointerReadiness.WindowKind
        var foregroundPID: pid_t? = nil
        var hit: NativeInputWindowHit? = nil
    }

    private func contentReadiness(
        foreground: Bool, kind: NativePointerIntent.Kind, declaredWindow: NativePointerReadiness.WindowKind? = nil,
        initialFocus: NativePointerReadiness.WindowFocus? = nil
    ) -> Readiness {
        // The declaration class is gesture-local. Rechecking labels for every
        // down/up would add slow provider RPCs to the event cadence. The real
        // object/process/window and physical hit remain checked for every event.
        let declared = declaredWindow ?? (foreground && kind != .scroll ? windowKind : .visualOnly)
        let focus = foreground && declared == .keyRequiring ? windowFocus() : .notRequired
        let prepared = !foreground && !BackgroundTarget.isExclusive
            && BackgroundEvents.pointerPreparationIsCurrent(target, window: window, needsKeyWindow: kind != .scroll)
        return Readiness(ready: NativePointerReadiness.ready(kind: kind, foreground: foreground, windowKind: declared,
            focus: focus, initialFocus: initialFocus, backgroundPrepared: prepared, exclusive: BackgroundTarget.isExclusive),
            foreground: foreground, focus: focus, windowKind: declared)
    }

    private func sampleReadiness(
        at point: CGPoint? = nil, kind: NativePointerIntent.Kind, delivery: NativeClickDelivery? = nil
    ) -> Readiness {
        let foregroundPID = NSWorkspace.shared.frontmostApplication?.processIdentifier
        let hit = foregroundPID == application.processIdentifier ? inputWindowHit(at: point) : nil
        let foreground = foregroundPID == application.processIdentifier && hit?.windowID == window.id
        var result: Readiness
        if (delivery == .systemPointer && !foreground) || (delivery == .windowDirected && BackgroundTarget.isExclusive)
        {
            result = Readiness(ready: false, foreground: foreground, focus: .notRequired, windowKind: .visualOnly)
        } else {
            result = contentReadiness(foreground: foreground, kind: kind)
        }
        result.foregroundPID = foregroundPID
        result.hit = hit
        return result
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
            "hitQueried": value.hit != nil, "hitWindow": value.hit?.windowID as Any? ?? NSNull(),
            "hitSource": value.hit?.source?.rawValue as Any? ?? NSNull(),
            "windowServerHit": value.hit.map { String(describing: $0.windowServer) } as Any? ?? NSNull(),
            "accessibilityHit": value.hit?.accessibility.map { String(describing: $0) } as Any? ?? NSNull()
        ])
    }

    func ready(at point: CGPoint? = nil, kind: NativePointerIntent.Kind = .click, delivery: NativeClickDelivery? = nil)
        -> Bool
    { sampleReadiness(at: point, kind: kind, delivery: delivery).ready }

    enum WindowPreparationOutcome: Equatable { case alreadyReady, contextPrepared }

    /// Prepare only the retained window context. An element caller must refresh
    /// its geometry before testing a point after this potentially moving phase.
    /// A context receipt never authorizes pointer delivery on its own.
    func prepareWindowContext(
        foreground: Bool, atNative point: CGPoint, kind: NativePointerIntent.Kind = .click,
        check: @escaping @MainActor () throws -> Void, onPreparation: @escaping @MainActor (String) -> Void,
        onCleanupUnknown: @escaping @MainActor () -> Void
    ) async throws -> WindowPreparationOutcome {
        try check()
        try target.validate(input: true)
        try BackgroundTarget.validateReleasedInput()
        try validateWindowIdentity()
        let initial = sampleReadiness(at: point, kind: kind)
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
                onAction: { onPreparation("foreground window activation") }, onCleanupUnknown: onCleanupUnknown)
        } else {
            do {
                try await NativeFocusPipeline.prepare(
                    target, window: window, nativeWindow: nativeWindow, needsKeyWindow: kind != .scroll,
                    onPreparation: onPreparation, onCleanupUnknown: onCleanupUnknown, beforeAction: check)
            } catch let error as DriverError where error.code == .inputUnavailable {
                try check()
                try target.validate(input: true)
                try BackgroundTarget.validateReleasedInput()
                throw NativePreparationUnavailable(description: error.description)
            }
        }
        try check()
        return .contextPrepared
    }

    /// QUERY: validate the caller's current point, without another activation,
    /// input event or preparation attempt.
    func requirePointReadiness(atNative point: CGPoint, kind: NativePointerIntent.Kind = .click) throws {
        let prepared = sampleReadiness(at: point, kind: kind)
        diagnoseReadiness(prepared, at: point, phase: "afterPreparation")
        guard prepared.ready else {
            throw NativePreparationUnavailable(description: "pointer route is not ready; no pointer event sent")
        }
    }

    /// Coordinate input keeps its original point and preparation/query order.
    func prepare(
        foreground: Bool, atNative point: CGPoint, kind: NativePointerIntent.Kind = .click,
        check: @escaping @MainActor () throws -> Void, onPreparation: @escaping @MainActor (String) -> Void,
        onCleanupUnknown: @escaping @MainActor () -> Void
    ) async throws {
        let outcome = try await prepareWindowContext(
            foreground: foreground, atNative: point, kind: kind, check: check, onPreparation: onPreparation,
            onCleanupUnknown: onCleanupUnknown)
        if outcome == .contextPrepared { try requirePointReadiness(atNative: point, kind: kind) }
    }

    func point(_ pixel: CGPoint) throws -> CGPoint {
        guard pixel.x >= 0, pixel.y >= 0, pixel.x < Double(window.width), pixel.y < Double(window.height) else {
            throw DriverError("pointer coordinates are outside the retained window image", code: .invalidRequest)
        }
        return CGPoint(
            x: window.frame.minX + pixel.x * window.frame.width / Double(window.width),
            y: window.frame.minY + pixel.y * window.frame.height / Double(window.height))
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

    func deliver(
        _ request: NativePointerIntent, check: @escaping @MainActor () throws -> Void, onDispatch: () throws -> Void,
        onPosted: () -> Void, onCleanupUnknown: @escaping () -> Void, delivery: NativeClickDelivery? = nil
    ) async throws {
        try validateParameters(request)
        try await deliver(
            request, from: point(request.start!), to: request.end.map(point), viewport: window.frame.size, check: check,
            onDispatch: onDispatch, onPosted: onPosted, onCleanupUnknown: onCleanupUnknown, delivery: delivery)
    }

    /// Geometry has one owner: an immutable image transform or a freshly
    /// resolved Element. Both use this exact event route and paired cleanup.
    func deliver(
        _ request: NativePointerIntent, from start: CGPoint, to end: CGPoint?, viewport: CGSize,
        check: @escaping @MainActor () throws -> Void, onDispatch: () throws -> Void, onPosted: () -> Void,
        onCleanupUnknown: @escaping () -> Void, delivery: NativeClickDelivery? = nil
    ) async throws {
        try check()
        try target.validate(input: true)
        try BackgroundTarget.validateReleasedInput()
        try validateWindowIdentity()
        let initial = sampleReadiness(at: start, kind: request.kind, delivery: delivery)
        try check()
        guard initial.ready else { throw DriverError("pointer route changed before dispatch", code: .inputUnavailable) }
        // Freeze one route for the entire gesture. A visible foreground target
        // receives ordinary HID input (including the global pointer position
        // some toolkits poll). Background windows remain process-addressed;
        // this branch never activates, retries or changes desktop mode.
        let source = try Input.source()
        let global = delivery.map { $0 == .systemPointer } ?? (BackgroundTarget.isExclusive || initial.foreground)
        if !global { try BackgroundEvents.requirePointerRoute(window: window) }
        guard let process = NativeProcessIdentity.read(window.pid) else {
            throw DriverError("pointer recipient disappeared", code: .staleTarget)
        }
        let sequence = try NativePointerEvents(
            request: request, from: start, to: end, viewport: viewport, source: source)
        let entry: CGEvent?
        if global {
            entry = nil
        } else {
            guard
                let value = NSEvent.enterExitEvent(
                    with: .mouseEntered,
                    location: NSPoint(x: start.x - window.frame.minX, y: window.frame.maxY - start.y),
                    modifierFlags: [], timestamp: ProcessInfo.processInfo.systemUptime, windowNumber: Int(window.id),
                    context: nil, eventNumber: 0, trackingNumber: 0, userData: nil)?.cgEvent
            else { throw DriverError("cannot allocate pointer entry event", code: .inputUnavailable) }
            entry = value
        }
        func post(_ event: CGEvent, point: CGPoint) {
            AgentPointer.current?.move(
                to: point, window: window,
                pulse: request.kind == .click
                    && [.leftMouseDown, .rightMouseDown, .otherMouseDown].contains(event.type), count: request.count,
                dragging: request.kind == .drag ? event.type != .leftMouseUp : nil)
            event.location = point
            if global {
                event.post(tap: .cghidEventTap)
            } else {
                BackgroundEvents.post(event, window: window, point: point, clickGroup: sequence.group)
            }
        }
        func validate(_ point: CGPoint, held: Bool) throws {
            try check()
            try target.validate(input: true)
            guard NativeProcessIdentity.read(window.pid) == process else {
                throw DriverError("pointer recipient changed", code: .staleTarget)
            }
            let button: CGMouseButton =
                request.button == "right" ? .right : request.button == "middle" ? .center : .left
            try BackgroundTarget.validateReleasedInput(allowing: global && held ? button : nil)
            if global {
                guard target.isForeground else {
                    throw DriverError(
                        "the selected application is no longer in the foreground", code: .inputUnavailable,
                        next: "read the desktop before continuing")
                }
                let hit = inputWindowHit(at: point)
                if hit.accessibility != nil {
                    // The public fallback can cross an AX messaging boundary.
                    // A cancellation/expired deadline during that read must stop
                    // this event; no global AX timeout is changed for the query.
                    try RequestDiagnostics.checkCancellation()
                }
                guard let destination = hit.windowID else {
                    throw DriverError(
                        "the pointer destination window could not be confirmed", code: .inputUnavailable,
                        next: "read the target window before continuing")
                }
                guard destination == window.id else {
                    throw DriverError(
                        "pointer location is covered by another window", code: .inputUnavailable,
                        next: "read the desktop and inspect the covering window")
                }
                let current = contentReadiness(
                    foreground: true, kind: request.kind, declaredWindow: initial.windowKind,
                    initialFocus: initial.focus)
                try check()
                guard current.ready else {
                    throw DriverError(
                        "the target window is no longer prepared for content pointer input", code: .inputUnavailable,
                        next: "read the target window before continuing")
                }
                // This exact foreground/window hit is the global route's
                // readiness proof; do not issue the same native hit query twice.
            } else {
                let current = contentReadiness(
                    foreground: foregroundWindow(at: point), kind: request.kind, declaredWindow: initial.windowKind,
                    initialFocus: initial.focus)
                try check()
                guard !BackgroundTarget.isExclusive, current.ready else {
                    throw DriverError("background pointer route changed", code: .inputUnavailable)
                }
            }
        }
        try await sequence.deliver(
            entry: entry, check: validate, post: post,
            canRelease: { global || NativeProcessIdentity.read(window.pid) == process }, skipped: onCleanupUnknown,
            onDispatch: onDispatch, onPosted: onPosted)
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
            event.setIntegerValueField(.mouseEventClickState, value: Int64(count))
            event.setIntegerValueField(.mouseEventNumber, value: group)
            event.setDoubleValueField(.mouseEventPressure, value: type == down || type == .leftMouseDragged ? 1 : 0)
            return event
        }
        var result = [Item(event: try mouse(.mouseMoved, at: from), release: nil, point: from, delayMs: 0)]
        switch request.kind {
        case .click:
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
            let count = max(8, Int(min(60, ceil(hypot(to.x - from.x, to.y - from.y) / 8))))
            var previous = from
            for step in 1...count {
                let fraction = Double(step) / Double(count)
                let point = CGPoint(x: from.x + (to.x - from.x) * fraction, y: from.y + (to.y - from.y) * fraction)
                // Check before converting to native integer deltas. Real window
                // geometry is much smaller; malformed input must never trap.
                let dx = (point.x - previous.x).rounded(), dy = (point.y - previous.y).rounded()
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
        onPosted: () -> Void
    ) async throws {
        var lastPoint = items[0].point
        var releaseMetric: NativeMetrics.Interval?
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
        try await pair.run {
            var held = false
            if let entry {
                try validate(lastPoint, false)
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
                    pair.release()
                    held = false
                    continue
                }
                try validate(item.point, held)
                lastPoint = item.point
                try onDispatch()
                if let release = item.release {
                    pair.press(event, release: release)
                    held = true
                } else {
                    post(event, lastPoint)
                }
                onPosted()
            }
        }
    }
}
