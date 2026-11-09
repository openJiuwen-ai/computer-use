// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

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
    var qtComboChoice: NativeQtComboChoice? = nil
    /// Only the selected qualified-popup adapter sets this. Ordinary pointers
    /// retain their existing fresh gesture geometry and exposure preparation.
    var frozenGeometry: Geometry? = nil
    var pointerRequirement: NativePointerReadiness.Requirement = .contentReceiver
    var requiresForegroundGeometry = false
    var requiresExactElementHit = false

    func requiring(
        _ requirement: NativePointerReadiness.Requirement, foregroundGeometry: Bool = false, exactElementHit: Bool = false
    ) -> Self {
        var unit = self
        unit.pointerRequirement = requirement
        unit.requiresForegroundGeometry = foregroundGeometry
        unit.requiresExactElementHit = exactElementHit
        return unit
    }

    /// Preparation, refreshed geometry and dispatch consume the same selected
    /// requirement. New local window units cannot silently restore the default.
    func pointerUnit(mapping: WindowGeometry.WindowMapping) -> NativePointerUnit {
        NativePointerUnit(
            application: application, window: mapping, nativeWindow: window,
            pointerRequirement: requiresForegroundGeometry ? .foregroundWindowPoint : pointerRequirement,
            recipientElement: member ?? element)
    }

    func exposurePointerUnit(_ exposure: NativeElementViewport.Exposure) -> NativePointerUnit {
        let mapping = exposure.mapping
        let process = BackgroundEvents.accessibilityProcessID(exposure.region)
        let remote = process.map { $0 != mapping.pid } == true
        return NativePointerUnit(
            application: application, window: mapping, nativeWindow: window,
            pointerRequirement: requiresForegroundGeometry ? .foregroundWindowPoint : pointerRequirement,
            recipientElement: remote ? exposure.region : nil)
    }

    static func allowsDispatchBasis(
        _ basis: NativeElementHitSelection.Basis, requirement: NativePointerReadiness.Requirement,
        exactOriginalHit: Bool, hasMember: Bool, kind: NativePointerIntent.Kind = .click,
        requiresExactElementHit: Bool = false
    ) -> Bool {
        if requiresExactElementHit { return basis == .exactHit && exactOriginalHit && !hasMember }
        if kind == .click, hasMember { return requirement == .contentReceiver && basis == .exactHit }
        guard requirement == .foregroundWindowPoint else { return basis != .rejected }
        return !hasMember && (basis == .exactHit && exactOriginalHit || basis == .ownedControlPart)
    }

    struct Geometry {
        let mapping: WindowGeometry.WindowMapping
        let frame: CGRect
        // The window/known scroll-ancestor intersection, not a claim that all
        // pixels are visible. AX hit evidence owns coverage at the chosen point.
        let visible: CGRect
        let candidate: NativeElementHitSelection.Candidate
        var serviceRecipient: NativePointerRecipient? = nil
        var point: CGPoint { candidate.point }

        func same(as other: Geometry) -> Bool {
            mapping.id == other.mapping.id && mapping.pid == other.mapping.pid && mapping.frame == other.mapping.frame
                && frame == other.frame && point == other.point
        }

        /// A live hit on the element or its proved passive part can retain the
        /// original point through a layout change. Geometry-only fallbacks and
        /// qualified popup proofs remain stricter.
        func permitsFrozenDispatch(
            from original: Geometry, basis: NativeElementHitSelection.Basis, requiresExactGeometry: Bool
        ) -> Bool {
            guard basis != .rejected, mapping.id == original.mapping.id, mapping.pid == original.mapping.pid,
                mapping.frame == original.mapping.frame, point == original.point, visible.contains(point)
            else { return false }
            return frame == original.frame || !requiresExactGeometry && (basis == .exactHit || basis == .ownedControlPart)
        }
    }

    struct Observation {
        let geometry: Geometry
        let hit: AXUIElement?
        let basis: NativeElementHitSelection.Basis
        let preparation: NativeElementHitSelection.Preparation
        var dispatchGeometryReady: Bool { basis != .rejected }
    }

    private struct Sample {
        let geometry: Geometry
        let selection: NativeElementHitSelection.Selection<AXUIElement>
        var hit: AXUIElement? { selection.observation.hit }
        var accepted: Bool { selection.basis != .rejected }
        var preparation: NativeElementHitSelection.Preparation { NativeElementHitSelection.preparation(selection) }
    }

    private func allowsDispatch(_ sampled: Sample) -> Bool {
        Self.allowsDispatchBasis(
            sampled.selection.basis, requirement: pointerRequirement,
            exactOriginalHit: sampled.hit.map { CFEqual($0, element) } == true, hasMember: member != nil, kind: kind,
            requiresExactElementHit: requiresExactElementHit)
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
        try NativeMetrics.measureDiagnostic(.clickViewportSampling) {
            try validatePhysicalWindow()
            let mapping = try NativeWindowUnit(application: application, window: window).mapping()
            return try NativeElementViewport.sample(element: element, window: window, mapping: mapping)
        }
    }

    func exposure() throws -> NativeElementViewport.Exposure? { try viewport().exposure }

    private func validatePhysicalWindow() throws {
        if let qtComboChoice {
            guard CFEqual(qtComboChoice.popup, window), try qtComboChoice.confirms() else {
                throw DriverError("the original Qt choice no longer belongs to its popup", code: .staleTarget)
            }
            return
        }
        if let logicalWindow, !CFEqual(logicalWindow, window) {
            guard let current = WindowGeometry.owningWindow(element, pid: application.processIdentifier, in: window),
                CFEqual(current, window)
            else { throw DriverError("the original popup element changed its physical window", code: .staleTarget) }
        }
    }

    func hasCurrentParentPath(
        from target: AXUIElement? = nil, to ancestor: AXUIElement? = nil, until deadline: TimeInterval
    ) throws -> Bool {
        try NativeElementHitSelection.reachesWindow(
            target ?? member ?? element, window: ancestor ?? window, same: { CFEqual($0, $1) },
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

    func inOriginalWindow(_ object: AXUIElement, pid: pid_t) -> Bool {
        var owner: pid_t = 0
        return AXUIElementGetPid(object, &owner) == .success && owner == pid
            && BackgroundEvents.accessibilityProcessID(object).map { $0 == pid } != false
            && AXAccess.element(object, kAXWindowAttribute).map { CFEqual($0, window) } == true
    }

    /// Member points use the member's own reported bounds. Window title and
    /// catalogue queries cannot improve that geometry.
    private func controlFrame(_ object: AXUIElement) -> CGRect? {
        guard let position = AXAccess.read(object, kAXPositionAttribute).value,
            let dimensions = AXAccess.read(object, kAXSizeAttribute).value,
            CFGetTypeID(position) == AXValueGetTypeID(), CFGetTypeID(dimensions) == AXValueGetTypeID()
        else { return nil }
        var origin = CGPoint.zero, size = CGSize.zero
        guard AXValueGetValue(unsafeDowncast(position, to: AXValue.self), .cgPoint, &origin),
            AXValueGetValue(unsafeDowncast(dimensions, to: AXValue.self), .cgSize, &size),
            [origin.x, origin.y, size.width, size.height].allSatisfy(\.isFinite), size.width > 0, size.height > 0
        else { return nil }
        return CGRect(origin: origin, size: size)
    }

    private func relatedControlHit(
        _ hit: AXUIElement, mapping: WindowGeometry.WindowMapping, frame: CGRect, until deadline: TimeInterval
    ) throws -> Bool {
        guard member == nil, logicalWindow == nil, qtComboChoice == nil, frozenGeometry == nil else { return false }
        func check() throws -> Bool {
            try RequestDiagnostics.checkCancellation()
            return ProcessInfo.processInfo.systemUptime < deadline
        }
        return try NativeOwnedElementHit.matches(
            element, hit: hit, frame: frame, same: CFEqual,
            role: { try check() ? AXAccess.read($0, kAXRoleAttribute).value as? String : nil },
            parent: { try check() ? AXAccess.element($0, kAXParentAttribute) : nil },
            inWindow: { try check() && inOriginalWindow($0, pid: mapping.pid) },
            passive: { object in
                var names: CFArray?
                guard try check(), AXUIElementCopyActionNames(object, &names) == .success,
                    let actions = names as? [String],
                    actions.allSatisfy({ [kAXShowMenuAction, "AXScrollToVisible"].contains($0) })
                else { return false }
                return try [kAXFocusedAttribute, kAXValueAttribute, kAXSelectedTextAttribute].allSatisfy { key in
                    guard try check() else { return false }
                    switch AXAccess.writable(object, key) {
                    case .value(false), .unsupported, .unavailable(.noValue): return true
                    default: return false
                    }
                }
            }, hitFrame: { try check() ? controlFrame($0) : nil })
    }

    private func sample(frozen: Geometry? = nil, in sampledViewport: NativeElementViewport? = nil) throws -> Sample {
        try RequestDiagnostics.checkCancellation()
        let viewport = try sampledViewport ?? self.viewport()
        let mapping = viewport.mapping
        let frame = viewport.frame
        let deadline = ProcessInfo.processInfo.systemUptime + 0.15
        // Scroll's element is the selected wheel region, not the requested
        // click control. Preserve its center and viewport for the public hit
        // refinement contract; click-specific search must not alter pages.
        let expand = kind == .click
        let visible = expand ? viewport.clipped : frame.intersection(mapping.frame)
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
                bounds: visible, activationPoint: expand ? activationPoint() : nil,
                memberBounds: expand ? member.flatMap(controlFrame) : nil, expand: expand)
        }
        let pointer = pointerUnit(mapping: mapping)
        let root = AXUIElementCreateApplication(application.processIdentifier)
        var boundedRoot: AXUIElement?
        var observations = [[String: Any]]()
        var serviceRecipient: NativePointerRecipient?
        let selected = try NativeElementHitSelection.select(
            candidates,
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
                    } else if CFEqual(hit, window) {
                        relation = .windowRoot
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
                if expand, relation == .otherControl, let hit,
                    try relatedControlHit(hit, mapping: mapping, frame: frame, until: deadline)
                {
                    relation = .ownedControlPart
                }
                if expand, candidate.source == .center, relation == .otherControl, let current = hit,
                    try NativeQtInlineListHitEvidence.sample(
                        target: member ?? element, hit: current, window: window, pid: application.processIdentifier,
                        windowID: mapping.id, at: candidate.point)
                {
                    // Keep the actual mismatching AX hit in the observation.
                    // This distinct basis records fresh provider stacking proof;
                    // it is not an exact AX hit or ordinary geometry fallback.
                    relation = .providerStackedTarget
                }
                var systemStatus: AXError?, systemWindow = false, coarseForeground = false, differentSystemHit = false
                var systemRelation: String?, systemHitPID: pid_t?
                // A provider can stop at the original window or a direct
                // layout parent. A sibling never qualifies for this query.
                if expand, status == .success, let current = hit,
                    try canQuerySystemHit(
                        current, relation: relation, candidate: candidate, pointer: pointer, until: deadline)
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
                        } else if member == nil, CFEqual(systemHit, current) {
                            systemWindow = CFEqual(systemHit, window)
                            systemRelation = systemWindow ? "originalWindow" : "originalButtonParent"
                            // The geometry attempt still needs the physical
                            // original foreground window after the AX query.
                            coarseForeground = pointer.foregroundWindow(at: candidate.point)
                            relation = .coarseAncestorWindow
                        } else {
                            differentSystemHit = true
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
                    // Other results do not refine the original app-scoped hit.
                    // A distinct returned element remains a conflict for the
                    // retained service route below.
                }
                if expand, candidate.permitsGeometryFallback, let hit,
                    NativeElementHitEvidence.permitsServiceAncestorProbe(
                        relation, systemStatus: systemStatus, differentSystemHit: differentSystemHit),
                    let process = BackgroundEvents.accessibilityProcessID(member ?? element), process != mapping.pid,
                    // The system read at the original root already required
                    // this strict parent path. A slow optional read must not
                    // exhaust a second path check. The endpoint proof below
                    // still rechecks live ownership and the exact host.
                    try (systemStatus != nil && CFEqual(hit, window) || hasCurrentParentPath(to: hit, until: deadline)),
                    coarseForeground || pointer.foregroundWindow(at: candidate.point),
                    let recipient = try NativePointerRecipient.coarseServiceHit(
                        hit, target: member ?? element, nativeWindow: window, host: mapping, point: candidate.point)
                {
                    relation = .serviceAncestor
                    serviceRecipient = recipient
                }
                let evidence = NativeElementHitEvidence.result(status, relation: relation)
                let originalWindowGeometry = try expand && candidate.permitsGeometryFallback
                    && evidence.relation == .unsupported
                    && pointer.foregroundWindow(at: candidate.point)
                    && hasCurrentParentPath(until: deadline)
                if WindowGeometry.inputDiagnosticsEnabled {
                    var observation: [String: Any] = [
                        "source": candidate.source.rawValue, "point": [candidate.point.x, candidate.point.y],
                        "axStatus": status.rawValue, "relation": evidence.relation.rawValue,
                        "originalWindowGeometry": originalWindowGeometry,
                        "elementFrame": [frame.minX, frame.minY, frame.width, frame.height],
                        "windowFrame": [mapping.frame.minX, mapping.frame.minY, mapping.frame.width, mapping.frame.height]
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
                    : candidate.permitsGeometryFallback && evidence.permitsBackgroundGeometry
                        ? pointer.foregroundWindow(at: candidate.point) : true
                return .init(
                    evidence: evidence, hit: status == .success ? hit : nil, foreground: foreground,
                    originalWindowGeometry: originalWindowGeometry)
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
            geometry: Geometry(
                mapping: mapping, frame: frame, visible: visible, candidate: selected.candidate,
                serviceRecipient: selected.basis == .serviceAncestorGeometry ? serviceRecipient : nil),
            selection: selected)
    }

    private func canQuerySystemHit(
        _ hit: AXUIElement, relation: NativeElementHitEvidence.Relation?, candidate: NativeElementHitSelection.Candidate,
        pointer: NativePointerUnit, until deadline: TimeInterval
    ) throws -> Bool {
        if let member {
            guard candidate.source == .memberCenter,
                relation == .windowRoot || relation == .otherControl,
                inOriginalWindow(member, pid: pointer.window.pid)
            else { return false }
            if CFEqual(hit, window) { return try hasCurrentParentPath(until: deadline) }
            return try inOriginalWindow(hit, pid: pointer.window.pid)
                && hasCurrentParentPath(to: hit, until: deadline)
                && (CFEqual(hit, element) || hasCurrentParentPath(from: hit, to: element, until: deadline))
        }
        guard candidate.source == .center else { return false }
        if relation != .windowRoot {
            guard relation == .otherControl,
                AXAccess.read(element, kAXRoleAttribute).value as? String == kAXButtonRole,
                AXAccess.read(hit, kAXRoleAttribute).value as? String == kAXGroupRole,
                AXAccess.element(element, kAXParentAttribute).map({ CFEqual($0, hit) }) == true,
                inOriginalWindow(element, pid: pointer.window.pid), inOriginalWindow(hit, pid: pointer.window.pid)
            else { return false }
        }
        return try hasCurrentParentPath(until: deadline) && pointer.foregroundWindow(at: candidate.point)
    }

    func geometry(prepared: Geometry? = nil) throws -> Geometry {
        if let prepared { return prepared }
        let sampled = try sample(frozen: frozenGeometry)
        // A distinct member is selected by the Python semantic resolver and
        // guarded by its native fact dependencies. This unit never searches
        // children or accepts a hit merely because it lies in the rectangle.
        guard allowsDispatch(sampled) else {
            throw DriverError(
                "no sampled pointer point belongs to this native target or its available window route",
                code: .inputUnavailable)
        }
        return sampled.geometry
    }

    /// Fresh exact-point evidence for the qualified popup boundary. This uses
    /// the same element hit and independent physical-window readers as delivery.
    func popupPhysicalSample(_ frozen: Geometry) throws -> NativePopupPointerSample<AXUIElement> {
        let sampled = try sample(frozen: frozen)
        let pointer = pointerUnit(mapping: sampled.geometry.mapping)
        let physical = pointer.physicalClickSample(at: frozen.point)
        return .init(
            geometry: sampled.geometry, hit: sampled.selection.basis == .exactHit ? sampled.hit : nil,
            physicalHit: physical.hit ?? .init(windowServer: .unavailable, accessibility: nil),
            foreground: physical.foreground && physical.ready)
    }

    /// Query projection keeps a mismatching native hit visible so the public
    /// resolver can prove an existing control-part relationship. It grants no
    /// permission to dispatch to that hit or to the owner's rectangle.
    /// Reuse a viewport only within the same synchronous query on this unit.
    func observedGeometry(in viewport: NativeElementViewport? = nil) throws -> Observation {
        let value = try sample(in: viewport)
        return Observation(
            geometry: value.geometry, hit: value.hit, basis: value.selection.basis, preparation: value.preparation)
    }

    /// The original window may first need to be uncovered. This query never
    /// accepts another control in that window, and dispatch still uses geometry().
    func preparationGeometry(in viewport: NativeElementViewport) throws -> Geometry {
        let observed = try observedGeometry(in: viewport)
        guard observed.preparation != .unavailable, !requiresExactElementHit || observed.basis == .exactHit else {
            throw DriverError(
                "no sampled pointer point belongs to this native target or its available window route",
                code: .inputUnavailable)
        }
        return observed.geometry
    }

    func backgroundPreparationAvailable() throws -> Bool {
        let sampled = try sample()
        guard sampled.preparation != .foreground else { return false }
        let pointer = pointerUnit(mapping: sampled.geometry.mapping)
        return pointer.backgroundPreparationAvailable(kind: kind)
    }

    func conditions() throws -> (
        readiness: NativeReadiness, foreground: Bool, geometry: Bool, route: Bool, hit: AXUIElement?
    ) {
        let sampled: Sample
        do { sampled = try sample() } catch let error as DriverError
            where error.code == .inputUnavailable || error.code == .unsupportedOperation
        { return (NativeReadiness([("geometry", "unknown")]), false, false, false, nil) }
        let pointer = pointerUnit(mapping: sampled.geometry.mapping)
        if !allowsDispatch(sampled) {
            let preparable = sampled.preparation == .foreground
            let readiness = NativeReadiness(
                [(preparable ? "foreground" : "receiver", "no")], preparationAllowed: preparable)
            return (readiness, pointer.foregroundWindow(at: sampled.geometry.point), true, false, sampled.hit)
        }
        let delivery = pointer.resolvedDelivery(nil)
        let foreground = pointer.foregroundWindow(at: sampled.geometry.point),
            route = pointer.ready(at: sampled.geometry.point, kind: kind, delivery: delivery)
        let required = [("receiver", route ? "yes" : "no")]
        return (NativeReadiness(required), foreground, true, route, sampled.hit)
    }

    private func validate(_ selected: Geometry, check: () throws -> Void) throws {
        try check()
        // Once a gesture has selected a point, validate that same point. Never
        // move a second down to a newly discovered candidate after input began.
        let sampled = try sample(frozen: selected)
        guard allowsDispatch(sampled), sampled.geometry.permitsFrozenDispatch(
            from: selected, basis: sampled.selection.basis,
            requiresExactGeometry: frozenGeometry != nil || kind != .click)
        else {
            throw DriverError("element pointer geometry changed during the sequence", code: .inputUnavailable)
        }
        if let service = selected.serviceRecipient, sampled.selection.basis != .exactHit,
            sampled.geometry.serviceRecipient.map({ service.matchesService($0) }) != true
        {
            throw DriverError("the original service no longer confirms the frozen pointer point", code: .inputUnavailable)
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
                    selected.mapping.frame.minX, selected.mapping.frame.minY, selected.mapping.frame.width,
                    selected.mapping.frame.height
                ],
                "elementFrame": [selected.frame.minX, selected.frame.minY, selected.frame.width, selected.frame.height],
                "point": [selected.point.x, selected.point.y]
            ])
        }
        if !sampled.accepted {
            guard sampled.preparation != .unavailable else {
                throw DriverError(
                    "no sampled pointer point belongs to this native target or its available window route",
                    code: .inputUnavailable)
            }
            guard foreground else {
                // No background primitive in this unit changes window order.
                // Record unavailability before Python may choose authorized
                // foreground preparation; no requested input has been sent.
                throw NativePreparationUnavailable(
                    description: "the element hit needs foreground window preparation; no pointer event sent")
            }
        }
        // Occlusion and ordinary preparation use the same exact-window
        // contract, including explicit key preparation for standard documents.
        let pointer = pointerUnit(mapping: selected.mapping)
        let delivery = pointer.resolvedDelivery(nil)
        do {
            let outcome = try await pointer.prepareWindowContext(
                foreground: foreground, atNative: selected.point, kind: kind, delivery: delivery, check: check,
                onPreparation: onPreparation, onCleanupUnknown: onCleanupUnknown)
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
                        current.mapping.frame.minX, current.mapping.frame.minY, current.mapping.frame.width,
                        current.mapping.frame.height
                    ],
                    "elementFrame": [current.frame.minX, current.frame.minY, current.frame.width, current.frame.height],
                    "point": [current.point.x, current.point.y]
                ])
            }
            if outcome == .contextPrepared || !current.same(as: selected) {
                // The public AX fallback also binds its retained frame, so a
                // new point must use the refreshed mapping of this exact window.
                let refreshed = pointerUnit(mapping: current.mapping)
                try refreshed.requirePointReadiness(atNative: current.point, kind: kind, delivery: delivery)
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
        _ request: NativePointerIntent, check: @escaping @MainActor () throws -> Void,
        guardOperation: @escaping @MainActor () throws -> Void, onDispatch: () throws -> Void,
        onPosted: () -> Void, onCleanupUnknown: @escaping @MainActor () -> Void, delivery: NativeClickDelivery? = nil,
        onPreparation: @escaping @MainActor (String) -> Void = { _ in },
        onNativePreparation: @escaping @MainActor (NativePreparationEffect) -> Void = { _ in },
        preparedGeometry: Geometry? = nil
    ) async throws {
        guard request.kind == kind, request.start == nil, request.end == nil else {
            throw DriverError("element pointer contract requires its original addressed intent", code: .invalidRequest)
        }
        try check()
        let selected = try geometry(prepared: preparedGeometry)
        let pointer = pointerUnit(mapping: selected.mapping)
        let cellContinuation: NativeCellPointerContinuation?
        if request.kind == .click, request.button == "left", request.count == 2, member == nil,
            qtComboChoice == nil, selected.serviceRecipient == nil
        {
            cellContinuation = try NativeCellPointerContinuation(cell: element, window: window, geometry: selected)
        } else { cellContinuation = nil }
        let continueSecondClick: ((CGPoint) throws -> WindowGeometry.WindowMapping?)? = cellContinuation.map { cell in
            { point in
                try check()
                return try cell.resolve(at: point)
            }
        }
        try await pointer.deliver(
            request, from: selected.point, to: nil, viewport: selected.visible.size,
            check: { try validate(selected, check: check) }, guardOperation: guardOperation,
            onDispatch: onDispatch, onPosted: onPosted,
            onCleanupUnknown: onCleanupUnknown, delivery: delivery,
            onPreparation: onPreparation, onNativePreparation: onNativePreparation,
            confirmedService: selected.serviceRecipient, continueSecondClick: continueSecondClick)
    }
}
