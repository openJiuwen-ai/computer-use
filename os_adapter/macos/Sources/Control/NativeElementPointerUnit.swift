import AppKit
import ApplicationServices

/// Addressed pointer input on one retained native geometry object. A click
/// uses its control; a scroll uses Python's selected scroll area and hit member.
/// This unit never chooses another target/member or invokes a semantic AX action.
@MainActor struct NativeElementPointerUnit {
    let application: NSRunningApplication
    let window: AXUIElement
    let element: AXUIElement
    var member: AXUIElement? = nil
    var kind: NativePointerIntent.Kind = .click
    var logicalWindow: AXUIElement? = nil

    struct Geometry {
        let mapping: WindowGeometry.WindowMapping
        let frame: CGRect
        // The window/known scroll-ancestor intersection, not a claim that all
        // pixels are visible. AX hit evidence owns coverage at the chosen point.
        let visible: CGRect
        let candidate: NativeElementHitSelection.Candidate
        var point: CGPoint { candidate.point }

        func same(as other: Geometry) -> Bool {
            mapping.id == other.mapping.id && mapping.pid == other.mapping.pid && mapping.frame == other.mapping.frame
                && frame == other.frame && point == other.point
        }
    }

    private struct Sample {
        let geometry: Geometry
        let selection: NativeElementHitSelection.Selection<AXUIElement>
        var hit: AXUIElement? { selection.observation.hit }
        var accepted: Bool { selection.basis != .rejected }
        var covered: Bool { selection.observation.evidence.relation == .otherWindow }
    }

    private func activationPoint() -> CGPoint? {
        guard let raw = AXAccess.read(member ?? element, "AXActivationPoint").value,
            CFGetTypeID(raw) == AXValueGetTypeID()
        else { return nil }
        var point = CGPoint.zero
        // Treat the client-provided AXValue as a point hint, without guessing
        // an AppKit coordinate transform. An exact AX hit must verify the hint.
        return AXValueGetValue(unsafeDowncast(raw, to: AXValue.self), .cgPoint, &point) ? point : nil
    }

    func viewport() throws -> NativeElementViewport {
        try validatePhysicalWindow()
        let mapping = try NativeWindowUnit(application: application, window: window).mapping()
        return try NativeElementViewport.sample(element: element, window: window, mapping: mapping)
    }

    func exposure() throws -> NativeElementViewport.Exposure? { try viewport().exposure }

    private func validatePhysicalWindow() throws {
        if let logicalWindow, !CFEqual(logicalWindow, window) {
            guard let current = WindowGeometry.owningWindow(
                element, pid: application.processIdentifier, in: window), CFEqual(current, window)
            else { throw DriverError("the original popup element changed its physical window", code: .staleTarget) }
        }
    }

    private func hasCurrentParentPathToWindow(until deadline: TimeInterval) throws -> Bool {
        try NativeElementHitSelection.reachesWindow(
            member ?? element, window: window, same: { CFEqual($0, $1) },
            parent: { AXAccess.element($0, kAXParentAttribute) },
            isSurface: { node in
                guard let role = AXAccess.read(node, kAXRoleAttribute).value as? String else { return true }
                return [kAXWindowRole, kAXSheetRole, "AXPopover"].contains(role)
            },
            canContinue: {
                try RequestDiagnostics.checkCancellation()
                return ProcessInfo.processInfo.systemUptime < deadline
            })
    }

    private func sample(frozen: Geometry? = nil) throws -> Sample {
        try RequestDiagnostics.checkCancellation()
        try validatePhysicalWindow()
        let mapping = try NativeWindowUnit(application: application, window: window).mapping()
        let viewport = try NativeElementViewport.sample(element: element, window: window, mapping: mapping)
        let frame = viewport.frame
        let deadline = ProcessInfo.processInfo.systemUptime + 0.15
        // Scroll's element is the selected wheel region, not the requested
        // click control. Preserve its center and viewport for the public hit
        // refinement contract; click-specific search must not alter pages.
        let expand = kind == .click
        let visible =
            expand
            ? viewport.clipped : frame.intersection(mapping.frame)
        guard !visible.isNull, visible.width > 0, visible.height > 0 else {
            throw DriverError(
                "the selected element is outside its window or known scrolling bounds", code: .inputUnavailable)
        }
        let candidates: [NativeElementHitSelection.Candidate]
        if let frozen {
            guard visible.contains(frozen.point) else {
                throw DriverError("element pointer geometry changed during the sequence", code: .inputUnavailable)
            }
            candidates = [frozen.candidate]
        } else {
            candidates = NativeElementHitSelection.candidates(
                bounds: visible, activationPoint: expand ? activationPoint() : nil, expand: expand)
        }
        let pointer = NativePointerUnit(application: application, window: mapping, nativeWindow: window)
        let root = AXUIElementCreateApplication(application.processIdentifier)
        var boundedRoot: AXUIElement?
        var observations = [[String: Any]]()
        let selected = try NativeElementHitSelection.select(
            candidates, exclusive: BackgroundTarget.isExclusive,
            canContinue: { candidate in
                try RequestDiagnostics.checkCancellation()
                return frozen != nil || candidate.source == .center || ProcessInfo.processInfo.systemUptime < deadline
            },
            query: { candidate in
                let queryRoot: AXUIElement
                if let timeout = candidate.queryTimeout {
                    if let boundedRoot {
                        queryRoot = boundedRoot
                    } else {
                        let additional = AXUIElementCreateApplication(application.processIdentifier)
                        AXUIElementSetMessagingTimeout(additional, timeout)
                        boundedRoot = additional
                        queryRoot = additional
                    }
                } else {
                    queryRoot = root
                }
                // Apple scopes messaging timeout to this exact AX object, not
                // equal objects. A separate root preserves the original center
                // route without relying on successful timeout restoration.
                var hit: AXUIElement?
                let status = AXUIElementCopyElementAtPosition(
                    queryRoot, Float(candidate.point.x), Float(candidate.point.y), &hit)
                try RequestDiagnostics.checkCancellation()
                var relation: NativeElementHitEvidence.Relation?
                if status == .success, let hit {
                    if CFEqual(hit, member ?? element) {
                        relation = .target
                    } else if let owner = WindowGeometry.owningWindow(
                        hit, pid: application.processIdentifier, in: window)
                    {
                        relation = CFEqual(owner, window) ? .otherControl : .otherWindow
                    } else {
                        relation = .unknownOwner
                    }
                } else {
                    relation = nil
                }
                var systemStatus: AXError?, systemWindow = false, coarseForeground = false
                var systemRelation: String?, systemHitPID: pid_t?
                // A provider can return its original window without descending
                // into an exposed child. This is not a known sibling hit. Try
                // the independent system-wide query only for that exact case;
                // normal exact hits retain their original query and contract.
                if expand, candidate.source == .center, status == .success, let current = hit, CFEqual(current, window),
                    try hasCurrentParentPathToWindow(until: deadline), pointer.foregroundWindow(at: candidate.point)
                {
                    let system = AXUIElementCreateSystemWide()
                    // Setting timeout on a system-wide object changes this
                    // process's GLOBAL AX timeout. Keep the existing policy;
                    // the original request deadline is checked around the read.
                    try RequestDiagnostics.checkCancellation()
                    var systemHit: AXUIElement?
                    let queried = AXUIElementCopyElementAtPosition(
                        system, Float(candidate.point.x), Float(candidate.point.y), &systemHit)
                    systemStatus = queried
                    systemRelation = NativeElementHitEvidence.result(queried, relation: nil).relation.rawValue
                    try RequestDiagnostics.checkCancellation()
                    if queried == .success, let systemHit {
                        if CFEqual(systemHit, member ?? element) {
                            systemRelation = "target"
                            hit = systemHit
                            relation = .target
                        } else if CFEqual(systemHit, window) {
                            systemRelation = "originalWindow"
                            systemWindow = true
                            // The geometry attempt still needs the physical
                            // original foreground window after the AX query.
                            coarseForeground = pointer.foregroundWindow(at: candidate.point)
                            relation = .coarseAncestorWindow
                        } else {
                            systemRelation = "differentElement"
                            if WindowGeometry.inputDiagnosticsEnabled {
                                var pid: pid_t = 0
                                if AXUIElementGetPid(systemHit, &pid) == .success {
                                    systemHitPID = pid
                                    if pid != application.processIdentifier { systemRelation = "otherApplication" }
                                }
                            }
                        }
                    }
                    // All other system results preserve the original rejected
                    // mismatch. They cannot enter background geometry fallback.
                }
                let evidence = NativeElementHitEvidence.result(status, relation: relation)
                if WindowGeometry.inputDiagnosticsEnabled {
                    var observation: [String: Any] = [
                        "source": candidate.source.rawValue, "point": [candidate.point.x, candidate.point.y],
                        "axStatus": status.rawValue, "relation": evidence.relation.rawValue
                    ]
                    if let systemStatus {
                        observation["systemWideStatus"] = systemStatus.rawValue
                        observation["systemWideOriginalWindow"] = systemWindow
                        observation["systemWideRelation"] = systemRelation
                        if let systemHitPID { observation["systemWideHitPID"] = systemHitPID }
                    }
                    observations.append(observation)
                }
                // The coarse ancestor case above and existing background
                // geometry fallback use physical-window evidence. Exact hits
                // and ordinary rejected points add no such query here; every
                // delivery route still checks its final window later.
                let foreground =
                    evidence.relation == .coarseAncestorWindow
                    ? coarseForeground
                    : !BackgroundTarget.isExclusive && candidate.permitsGeometryFallback
                        && evidence.permitsBackgroundGeometry ? pointer.foregroundWindow(at: candidate.point) : true
                return .init(evidence: evidence, hit: status == .success ? hit : nil, foreground: foreground)
            })
        guard let selected else {
            throw DriverError("the selected element has no usable pointer point", code: .inputUnavailable)
        }
        if WindowGeometry.inputDiagnosticsEnabled {
            diagnose([
                "kind": "element_pointer_hit", "window": mapping.id, "frozen": frozen != nil,
                "basis": selected.basis.rawValue,
                "selectedPoint": [selected.candidate.point.x, selected.candidate.point.y], "candidates": observations
            ])
        }
        try RequestDiagnostics.checkCancellation()
        return Sample(
            geometry: Geometry(mapping: mapping, frame: frame, visible: visible, candidate: selected.candidate),
            selection: selected)
    }

    func geometry() throws -> Geometry {
        let sampled = try sample()
        // A distinct member is selected by the Python semantic resolver and
        // guarded by its native fact dependencies. This unit never searches
        // children or accepts a hit merely because it lies in the rectangle.
        guard sampled.accepted else {
            throw DriverError(
                "no sampled pointer point belongs to this native target or its available window route",
                code: .inputUnavailable)
        }
        return sampled.geometry
    }

    /// Query projection keeps a mismatching native hit visible so the public
    /// resolver can prove an existing control-part relationship. It grants no
    /// permission to dispatch to that hit or to the owner's rectangle.
    func observedGeometry(allowWindowPreparation: Bool = false) throws -> (geometry: Geometry?, hit: AXUIElement?) {
        let value = try sample()
        let preparable = allowWindowPreparation && value.covered
        return (value.accepted || preparable ? value.geometry : nil, value.hit)
    }

    /// The original window may first need to be uncovered. This query never
    /// accepts another control in that window, and dispatch still uses geometry().
    func preparationGeometry() throws -> Geometry {
        guard let value = try observedGeometry(allowWindowPreparation: true).geometry else {
            throw DriverError(
                "no sampled pointer point belongs to this native target or its available window route",
                code: .inputUnavailable)
        }
        return value
    }

    func conditions() throws -> (
        readiness: NativeReadiness, foreground: Bool, geometry: Bool, route: Bool, hit: AXUIElement?
    ) {
        let sampled: Sample
        do { sampled = try sample() } catch let error as DriverError
            where error.code == .inputUnavailable || error.code == .modeUnsupported
        { return (NativeReadiness([("geometry", "unknown")]), false, false, false, nil) }
        guard sampled.accepted else {
            if sampled.covered { return (NativeReadiness([("receiver", "no")]), false, true, false, sampled.hit) }
            return (NativeReadiness([("geometry", "unknown")]), false, false, false, sampled.hit)
        }
        let pointer = NativePointerUnit(
            application: application, window: sampled.geometry.mapping, nativeWindow: window)
        let foreground = pointer.foregroundWindow(at: sampled.geometry.point),
            route = pointer.ready(at: sampled.geometry.point, kind: kind)
        var required = [("receiver", route ? "yes" : "no")]
        if BackgroundTarget.isExclusive { required.append(("foreground", foreground ? "yes" : "no")) }
        return (NativeReadiness(required), foreground, true, route, sampled.hit)
    }

    private func validate(_ selected: Geometry, check: () throws -> Void) throws {
        try check()
        // Once a gesture has selected a point, validate that same point. Never
        // move a second down to a newly discovered candidate after input began.
        let sampled = try sample(frozen: selected)
        guard sampled.accepted, sampled.geometry.same(as: selected) else {
            throw DriverError("element pointer geometry changed during the sequence", code: .inputUnavailable)
        }
    }

    func prepare(
        foreground: Bool, check: @escaping @MainActor () throws -> Void,
        onPreparation: @escaping @MainActor (String) -> Void, onCleanupUnknown: @escaping @MainActor () -> Void
    ) async throws {
        try check()
        let sampled = try sample()
        let selected = sampled.geometry
        if WindowGeometry.inputDiagnosticsEnabled {
            diagnose([
                "kind": "element_pointer_preparation_geometry", "phase": "beforePreparation",
                "window": selected.mapping.id,
                "windowFrame": [
                    selected.mapping.frame.minX, selected.mapping.frame.minY,
                    selected.mapping.frame.width, selected.mapping.frame.height
                ],
                "elementFrame": [selected.frame.minX, selected.frame.minY, selected.frame.width, selected.frame.height],
                "point": [selected.point.x, selected.point.y]
            ])
        }
        if !sampled.accepted {
            guard sampled.covered else {
                throw DriverError(
                    "no sampled pointer point belongs to this native target or its available window route",
                    code: .inputUnavailable)
            }
            guard foreground else {
                // No background primitive in this unit changes window order.
                // Record unavailability before Python may choose authorized
                // foreground preparation; no requested input has been sent.
                throw NativePreparationUnavailable(
                    description: "another window covers the element's native hit target; no pointer event sent")
            }
        }
        // Occlusion and ordinary preparation use the same exact-window
        // contract, including explicit key preparation for standard documents.
        let pointer = NativePointerUnit(application: application, window: selected.mapping, nativeWindow: window)
        do {
            let outcome = try await pointer.prepareWindowContext(
                foreground: foreground, atNative: selected.point, kind: kind, check: check,
                onPreparation: onPreparation,
                onCleanupUnknown: onCleanupUnknown)
            // Preparation may move the same original window or element. No
            // requested mouse event has been sent, so refresh before admitting
            // a point; only actual delivery freezes its gesture coordinates.
            try check()
            let current = try geometry()
            guard current.mapping.id == selected.mapping.id, current.mapping.pid == selected.mapping.pid else {
                throw DriverError(
                    "the original element's pointer window changed during preparation", code: .staleTarget)
            }
            if WindowGeometry.inputDiagnosticsEnabled {
                diagnose([
                    "kind": "element_pointer_preparation_geometry", "phase": "afterRefresh",
                    "window": current.mapping.id,
                    "windowFrame": [
                        current.mapping.frame.minX, current.mapping.frame.minY,
                        current.mapping.frame.width, current.mapping.frame.height
                    ],
                    "elementFrame": [current.frame.minX, current.frame.minY, current.frame.width, current.frame.height],
                    "point": [current.point.x, current.point.y]
                ])
            }
            if outcome == .contextPrepared || !current.same(as: selected) {
                // The public AX fallback also binds its retained frame, so a
                // new point must use the refreshed mapping of this exact window.
                let refreshed = NativePointerUnit(
                    application: application, window: current.mapping, nativeWindow: window)
                try refreshed.requirePointReadiness(atNative: current.point, kind: kind)
            }
        } catch {
            if WindowGeometry.inputDiagnosticsEnabled {
                // A diagnostic after the failed decision, never a new input
                // admission or retry. Do not relabel this later geometry as
                // the evidence used by NativePointerUnit's earlier decision.
                let current = WindowGeometry.hint(element)?.frame
                let row = WindowServerInfo.window(selected.mapping.id)
                diagnose([
                    "kind": "element_pointer_preparation_geometry", "phase": "postFailure",
                    "window": selected.mapping.id,
                    "elementFrame": current.map { [$0.minX, $0.minY, $0.width, $0.height] } as Any? ?? NSNull(),
                    "windowBounds": row?[kCGWindowBounds as String] as Any? ?? NSNull()
                ])
            }
            throw error
        }
    }

    func deliver(
        _ request: NativePointerIntent, check: @escaping @MainActor () throws -> Void, onDispatch: () throws -> Void,
        onPosted: () -> Void, onCleanupUnknown: @escaping () -> Void, delivery: NativeClickDelivery? = nil
    ) async throws {
        guard request.kind == kind, request.start == nil, request.end == nil else {
            throw DriverError("element pointer contract requires its original addressed intent", code: .invalidRequest)
        }
        try check()
        let selected = try geometry()
        let pointer = NativePointerUnit(application: application, window: selected.mapping, nativeWindow: window)
        try await pointer.deliver(
            request, from: selected.point, to: nil, viewport: selected.visible.size,
            check: { try validate(selected, check: check) }, onDispatch: onDispatch, onPosted: onPosted,
            onCleanupUnknown: onCleanupUnknown, delivery: delivery)
    }
}
