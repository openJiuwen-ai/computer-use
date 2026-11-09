// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices

/// Borrowed scope, containment and unit construction. This service owns no
/// registry, operation journal, deadline or route selection state.
@MainActor final class NativeScopeResolver: NativeScopeAccess, NativeInputUnitAccess {
    private let session: String
    private let applications: NativeApplicationAccess
    private let appSelections: AppSelections
    let sharing: WindowSharing
    let desktop: () -> DesktopCapture
    private let directory: NativeDirectory
    private let pointer: AgentPointer
    let captures: NativeCapture
    init(
        session: String, applications: NativeApplicationAccess, appSelections: AppSelections, sharing: WindowSharing,
        desktop: @escaping () -> DesktopCapture, directory: NativeDirectory, captures: NativeCapture,
        pointer: AgentPointer
    ) {
        self.session = session
        self.applications = applications
        self.appSelections = appSelections
        self.sharing = sharing
        self.desktop = desktop
        self.directory = directory
        self.captures = captures
        self.pointer = pointer
    }

    func requireSession(_ value: String) throws {
        guard value == session else { throw DriverError("request belongs to another session", code: .staleTarget) }
    }

    /// Resolve once, independently of input admission. The subsequent global
    /// event uses this screen point without following or checking the object.
    func locatePoint(_ raw: Any?) throws -> [String: Any] {
        let request = try object(raw, keys: ["target", "point"])
        let target = try NativeTarget(request["target"])
        try target.requireSession(session)
        try RequestDiagnostics.checkCancellation()
        let result: CGPoint
        if let capture = target.capture {
            guard target.node == nil else {
                throw DriverError("a point locator cannot mix image and element references", code: .invalidRequest)
            }
            let pixel = try object(request["point"], keys: ["x", "y"])
            let point = CGPoint(x: try number(pixel["x"], label: "point.x"), y: try number(pixel["y"], label: "point.y"))
            if target.isDesktopImage {
                guard target.instance == nil, target.rootWindow == nil else {
                    throw DriverError("desktop point locator has an application identity", code: .invalidRequest)
                }
                let mapping = try desktop().observed(capture)
                result = try mapping.geometry.point(x: point.x, y: point.y, captured: mapping.captured)
            } else {
                let entry = try captures.retained(capture)
                guard NSDictionary(dictionary: entry.binding).isEqual(to: target.wire),
                    !entry.surface.owner.isTerminated
                else {
                    throw DriverError("image point belongs to another binding", code: .staleTarget)
                }
                let original = entry.mapping
                guard let row = WindowServerInfo.window(original.id),
                    let pid = (row[kCGWindowOwnerPID as String] as? NSNumber)?.int32Value,
                    let bounds = row[kCGWindowBounds as String] as? [String: Any],
                    let frame = CGRect(dictionaryRepresentation: bounds as CFDictionary)
                else { throw ObservationChanged("point target window is unavailable") }
                let current = WindowGeometry.WindowMapping(
                    id: original.id, pid: pid, frame: frame, title: original.title,
                    width: original.width, height: original.height)
                result = try NativeCapturedPointerPlan.resolve(original: original, current: current).point(point)
            }
        } else {
            guard request["point"] is NSNull, let node = target.node else {
                throw DriverError("element point locator requires an element and no image point", code: .invalidRequest)
            }
            let element = try applications.pointElement(node, scope: target.surface)
            var position = CGPoint.zero, size = CGSize.zero
            guard let origin = AXAccess.read(element, kAXPositionAttribute).value,
                let dimensions = AXAccess.read(element, kAXSizeAttribute).value,
                CFGetTypeID(origin) == AXValueGetTypeID(), CFGetTypeID(dimensions) == AXValueGetTypeID(),
                AXValueGetValue(unsafeDowncast(origin, to: AXValue.self), .cgPoint, &position),
                AXValueGetValue(unsafeDowncast(dimensions, to: AXValue.self), .cgSize, &size),
                [position.x, position.y, size.width, size.height].allSatisfy(\.isFinite), size.width > 0, size.height > 0
            else { throw DriverError("original element has no usable frame", code: .observationIncomplete) }
            result = CGPoint(x: position.x + size.width / 2, y: position.y + size.height / 2)
        }
        try RequestDiagnostics.checkCancellation()
        return ["point": ["x": result.x, "y": result.y]]
    }

    func observationSurface(_ binding: [String: Any]) throws -> NativeDirectory.Surface {
        try sharing.checkAvailable()
        return try directory.windowScope(
            instance: string(binding["instance"], label: "instance"),
            root: string(binding["rootWindow"], label: "rootWindow"),
            surface: string(binding["surface"], label: "surface")).surface
    }

    func retainDesktopWindow(_ native: NativeWindows.Window, owner: NSRunningApplication) -> [String: Any] {
        directory.retainDesktopWindow(native, owner: owner)
    }

    func observePointer(_ binding: [String: Any]) {
        guard let scope = binding["surface"] as? String, let mapping = directory.surface(scope)?.native?.mapping else {
            return
        }
        pointer.observe(mapping)
    }

    func aimPointer(_ binding: [String: Any], intent: NativeIntent, receiver: String?) {
        guard let ref = binding["node"] as? String ?? receiver else { return }
        let mapping = (binding["surface"] as? String).flatMap { directory.surface($0)?.native?.mapping }
        AgentPointer.current?.aim(
            element: applications.visualElement(ref), window: mapping,
            pulse: !["key", "scroll", "drag"].contains(intent.kind), count: intent.parameters["count"] as? Int ?? 1)
    }

    func inputScope(_ raw: Any?, canonicalize: Bool = false) throws -> NativeInputScope {
        let value = try NativeTarget(raw)
        try value.requireSession(session)
        let observed = try value.node.map { try applications.readRoot($0) }
        return try inputScope(value, canonicalize: canonicalize, observed: observed)
    }

    /// Optional restitution borrows retained ownership, never a fresh input
    /// boundary query on a target that the action may just have closed.
    func retainedForegroundSource(_ binding: [String: Any]) -> (application: NSRunningApplication, element: AXUIElement?)? {
        guard let scope = binding["surface"] as? String else { return nil }
        guard let application = directory.surface(scope)?.owner ?? directory.applicationRoot(scope)?.owner else { return nil }
        return (application, (binding["node"] as? String).flatMap { applications.visualElement($0) }
                ?? directory.surface(scope)?.element)
    }

    func fixedInputWindow(_ binding: [String: Any]) throws -> NativeFixedInputWindow? {
        let requested = try NativeTarget(binding)
        try requested.requireSession(session)
        let entry = try appSelections.entry(try string(requested.instance, label: "instance"))
        let id = try string(requested.surface, label: "surface")
        guard requested.rootWindow == entry.root else {
            throw DriverError("working root changed", code: .staleTarget)
        }
        guard directory.applicationRoot(id) == nil else { return nil }
        guard let selected = directory.surface(id), selected.owner.isEqual(entry.owner) else {
            throw DriverError("original mechanism window binding changed", code: .staleTarget)
        }
        if let node = requested.node {
            let read = try applications.readRoot(node)
            guard read.scope == id, read.application.isEqual(entry.owner) else {
                throw DriverError("node belongs to a different mechanism window", code: .staleTarget)
            }
        }
        // This is optional mechanism evidence, not another admission boundary.
        // The original physical identity must survive, but an incomplete AX
        // catalogue must not turn a missing proxy into a public "closed" error.
        if selected.native != nil {
            switch directory.nativeWindowPresence(selected) {
            case .ended: throw DriverError("original mechanism window lifetime ended", code: .staleTarget)
            case .live: break
            case .unknown, nil: return nil
            }
        }
        let windows = try applications.windows(
            entry.owner, nativeWindows: selected.native.map { [$0] } ?? [],
            contentRoot: selected.element, purpose: .inputValidation)
        let matches = windows.filter { $0.ref == selected.window }
        guard matches.count == 1, let window = matches.first else { return nil }
        if let native = selected.native {
            guard window.nativeID == native.mapping.id else { return nil }
        } else {
            // AX-only scopes retain their original exact object. Without a
            // physical ID there is no proof authorizing a replacement proxy.
            guard let original = selected.element, CFEqual(window.element, original) else { return nil }
        }
        return NativeFixedInputWindow(application: entry.owner, window: window.element)
    }

    private func inputScope(
        _ target: NativeTarget, canonicalize: Bool, observed: NativeApplicationReader.ReadRoot?,
        popupOwner: AXUIElement? = nil
    )
        throws -> NativeInputScope
    {
        var value = target
        try value.requireSession(session)
        let instance = try string(value.instance, label: "instance")
        let surface = try string(value.surface, label: "surface")
        guard value.capture == nil else {
            throw DriverError("native object actions do not consume a pixel capture", code: .invalidRequest)
        }
        let entry = try appSelections.entry(instance)
        if let observed {
            guard observed.scope == surface, observed.application.isEqual(entry.owner) else {
                throw DriverError("node belongs to a different surface", code: .staleTarget)
            }
        }
        if let menu = try directory.applicationRootScope(instance: instance, root: value.rootWindow, surface: surface) {
            guard menu.kind == .menuBar else {
                throw DriverError(
                    "the application content scope does not have an addressed input receiver", code: .inputUnavailable)
            }
            if let root = entry.root {
                let native = NativeWindows.listIfAvailable(entry.owner)
                let windows = try applications.windows(
                    entry.owner, nativeWindows: native ?? [],
                    contentRoot: directory.windowElement(root, owner: entry.owner), purpose: .inputValidation)
                guard let window = windows.first(where: { $0.ref == root })?.element else {
                    throw DriverError("the menu's original working window closed", code: .staleTarget)
                }
                let blocked = try NativeWindowBoundary.blocked(
                    root: root, target: root,
                    windows: windows.map { .init(ref: $0.ref, parent: $0.parent, modal: $0.modal == true) },
                    focused: applications.focusedWindow(entry.owner, windows: windows))
                return NativeInputScope(
                    wire: value.wire, application: entry.owner, window: window, menuRoot: menu.element, blocked: blocked,
                    windowEvidence: .init(windows: windows, nativeWindows: native)
                )
            }
            try BackgroundTarget(entry.owner).validateWindowlessMenu()
            return NativeInputScope(
                wire: value.wire, application: entry.owner, window: nil, menuRoot: menu.element, blocked: [])
        }
        guard let root = entry.root, let discovered = directory.surface(surface), discovered.owner.isEqual(entry.owner)
        else { throw DriverError("target has no bound working-window observation", code: .staleTarget) }
        if !(canonicalize && value.rootWindow == nil) {
            guard try string(value.rootWindow, label: "rootWindow") == root else {
                throw DriverError("working root changed", code: .staleTarget)
            }
        }
        let native = NativeWindows.list(entry.owner)
        let workingRoot = directory.windowElement(root, owner: entry.owner)
        // AXWindow deliberately names a sheet's attached parent AXWindow.
        // Retain the original node/surface binding, but prepare input in its
        // already observed nearest logical surface. Hosted proxies use the
        // existing exact-child identity proof, never titles or geometry.
        let interactionSurface = observed?.observedInteractionSurface.map {
            WindowGeometry.presentedWindow($0, pid: entry.owner.processIdentifier)
        }
        let contentRoot = try popupOwner ?? workingRoot.map { original in
            guard let selected = interactionSurface ?? discovered.element else { return original }
            return try NativeWindowBoundary.contentRoot(
                workingRoot: original, target: selected, same: { CFEqual($0, $1) },
                modal: { element in
                    try RequestDiagnostics.checkCancellation()
                    return AXAccess.read(element, kAXModalAttribute).value as? Bool
                        ?? (AXAccess.read(element, kAXRoleAttribute).value as? String == kAXSheetRole)
                },
                parent: { element in
                    try RequestDiagnostics.checkCancellation()
                    guard let parent = AXAccess.element(element, kAXParentAttribute) else { return nil }
                    var pid: pid_t = 0
                    return AXUIElementGetPid(parent, &pid) == .success && pid == entry.owner.processIdentifier
                        ? parent : nil
                })
        }
        var coverageComplete = true
        let noteIssue: (NativeApplicationReader.WindowIssue) -> Void = { _ in coverageComplete = false }
        var windows = try applications.windows(
            entry.owner, nativeWindows: native, contentRoot: contentRoot, purpose: .inputValidation,
            reportIssue: noteIssue)
        // The hint cannot replace proof that the original App root is live.
        // It may itself be a deep popover omitted by the narrower scan.
        if popupOwner == nil, let original = workingRoot, let contentRoot, !CFEqual(original, contentRoot),
            !windows.contains(where: { $0.ref == root })
        {
            coverageComplete = true
            windows = try applications.windows(
                entry.owner, nativeWindows: native, purpose: .inputValidation, reportIssue: noteIssue)
        }
        guard windows.contains(where: { $0.ref == root }), windows.contains(where: { $0.ref == discovered.window })
        else { throw DriverError("the original working window or surface closed", code: .staleTarget) }
        let graph = windows.map { AppSelection.Window(ref: $0.ref, parent: $0.parent, modal: $0.modal == true) }
        let candidate = interactionSurface.flatMap { surface in windows.first { CFEqual($0.element, surface) }?.ref }
        let window = NativeWindowBoundary.interactionWindow(
            observed: discovered.window, candidate: candidate, windows: graph)
        var ancestor: String? = window, seen = Set<String>()
        while let value = ancestor, value != root, seen.insert(value).inserted {
            ancestor = windows.first { $0.ref == value }?.parent
        }
        let appModal =
            ancestor != root && windows.contains { $0.ref == window && $0.modal == true }
            && AppSelection.current(root: root, windows: graph, focused: nil) == window
        guard ancestor == root || appModal else {
            throw DriverError("surface is outside the selected working window", code: .staleTarget)
        }
        guard let nativeWindow = windows.first(where: { $0.ref == window })?.element else {
            throw DriverError("the original interaction surface closed", code: .staleTarget)
        }
        value.rootWindow = root
        // Observation may choose an app-modal surface with unknown ownership;
        // it does not decide whether a fixed input target is blocked. A modal
        // on the target's ancestor path contains that target, not blocks it.
        // Preserve the existing admission of a uniquely observed app-modal,
        // then validate its live boundary without selecting a different view.
        let blocked = try NativeWindowBoundary.blocked(
            root: appModal ? window : root, target: window, windows: graph,
            focused: applications.focusedWindow(entry.owner, windows: windows))
        // AXChildren can expose a menu's list in the document while AXWindow
        // correctly names a separate popup. Keep the document/root binding and
        // the exact element; resolve only its physical input surface from a
        // fresh forward path. An unrelated document can never use this path.
        if let observed,
            let popup = WindowGeometry.owningWindow(
                observed.element, pid: entry.owner.processIdentifier, in: nativeWindow),
            !CFEqual(popup, nativeWindow), windows.contains(where: { CFEqual($0.element, popup) }),
            AXAccess.read(popup, kAXSubroleAttribute).value as? String != kAXStandardWindowSubrole,
            AXAccess.read(popup, kAXModalAttribute).value as? Bool != true,
            try LocalMenuAttachment.confirms(
                leaf: observed.element, ancestors: observed.ancestors, window: nativeWindow,
                same: { CFEqual($0, $1) }, role: { AXAccess.read($0, kAXRoleAttribute).value as? String },
                children: {
                    try RequestDiagnostics.checkCancellation()
                    guard let children = AXAccess.elements($0, kAXChildrenAttribute, limit: 2048) else {
                        throw DriverError("popup attachment cannot be confirmed", code: .inputUnavailable)
                    }
                    return children
                })
        {
            return NativeInputScope(
                wire: value.wire, application: entry.owner, window: popup, menuRoot: nil, blocked: blocked,
                windowEvidence: .init(windows: windows))
        }
        return NativeInputScope(
            wire: value.wire, application: entry.owner, window: nativeWindow, menuRoot: nil, blocked: blocked,
            windowEvidence: .init(windows: windows, coverageComplete: coverageComplete))
    }

    /// QUERY admission for an already selected, unconstrained local dispatch.
    /// Preparation and ordinary unit construction never call this extra port.
    func keyboardBoundaryAnchor(_ binding: [String: Any], application: NSRunningApplication, window: AXUIElement) throws
        -> NativeKeyboardBoundaryAnchor?
    {
        guard binding["node"] is NSNull, let instance = binding["instance"] as? String,
            let root = try appSelections.entry(instance).root, let surface = binding["surface"] as? String,
            let discovered = directory.surface(surface), discovered.window == root,
            discovered.owner.isEqual(application), let original = directory.windowElement(root, owner: application),
            CFEqual(original, window), let native = discovered.native,
            let process = NativeProcessIdentity.read(application.processIdentifier), native.process == process,
            BackgroundEvents.accessibilityProcessID(window) == application.processIdentifier,
            let id = BackgroundEvents.nativeWindowID(window), id == native.mapping.id,
            applications.knownWindowReference(window, app: application, process: process) == root,
            AXAccess.read(window, kAXRoleAttribute).value as? String == kAXWindowRole,
            AXAccess.read(window, kAXModalAttribute).value as? Bool == false
        else { return nil }
        return .init(
            binding: binding, application: application, process: process, rootReference: root, window: window,
            nativeID: id)
    }

    /// QUERY only. This port never starts the full-scope fallback itself.
    func keyboardBoundary(_ anchor: NativeKeyboardBoundaryAnchor) throws -> NativeKeyboardBoundaryDecision {
        try RequestDiagnostics.checkCancellation()
        try BackgroundTarget(anchor.application).validate(input: true)
        guard NativeProcessIdentity.read(anchor.process.pid) == anchor.process else {
            throw DriverError("original keyboard process changed", code: .staleTarget)
        }
        let target = try NativeTarget(anchor.binding)
        try target.requireSession(session)
        let entry = try appSelections.entry(try string(target.instance, label: "instance"))
        guard entry.owner.isEqual(anchor.application), entry.root == anchor.rootReference,
            target.rootWindow == anchor.rootReference
        else { throw DriverError("original keyboard working root changed", code: .staleTarget) }
        guard
            applications.knownWindowReference(anchor.window, app: anchor.application, process: anchor.process)
                == anchor.rootReference
        else { return .needsLegacy("original AX alias is no longer known") }
        guard let native = NativeWindows.listIfAvailable(anchor.application),
            native.contains(where: { $0.mapping.id == anchor.nativeID && $0.process == anchor.process })
        else { return .needsLegacy("original keyboard WindowServer identity needs full scope") }
        let hints = try directory.retainedModalHints(owner: anchor.application, native: native)
        let boundary = try applications.inputBoundary(
            anchor.application, nativeWindows: native, contentRoot: anchor.window, required: [anchor.window],
            modalHints: hints, expandMissingRequired: false)
        let nativeIDs = Set(boundary.windows.compactMap(\.nativeID))
        guard boundary.windows.contains(where: { CFEqual($0.element, anchor.window) && $0.nativeID == anchor.nativeID }),
            native.allSatisfy({ nativeIDs.contains($0.mapping.id) }), boundary.unresolvedModalHints.isEmpty
        else { return .needsLegacy("native window or retained modal needs full scope") }
        let projection = NativeKeyboardBoundaryProjection(
            windows: boundary.windows.map {
                .init(key: $0.ref, element: $0.element, role: $0.kind == "sheet" ? kAXSheetRole : $0.kind,
                      modal: $0.modal, parent: $0.parent, parentKnown: !$0.parentUnknown)
            },
            issue: boundary.issue)
        return NativeKeyboardBoundaryPolicy.decide(
            original: anchor.window, projection: projection, same: { CFEqual($0, $1) })
    }

    /// Explicit full-query port, selected only by the parent keyboard Gate.
    /// The original full collector may not replace the unit's captured AX object.
    func legacyKeyboardBoundary(_ anchor: NativeKeyboardBoundaryAnchor) throws -> [String] {
        guard NativeProcessIdentity.read(anchor.process.pid) == anchor.process else {
            throw DriverError("original keyboard process changed", code: .staleTarget)
        }
        let scope = try inputScope(anchor.binding)
        guard scope.application.isEqual(anchor.application), let window = scope.window, CFEqual(window, anchor.window)
        else { throw DriverError("original keyboard AX window changed", code: .staleTarget) }
        return scope.blocked
    }

    func checkContainment(_ binding: [String: Any], within: [String], op: NativeOperation) throws {
        guard !within.isEmpty else { return }
        let node = try string(binding["node"], label: "node")
        let read = try applications.readRoot(node)
        if let menu = directory.applicationRoot(read.scope), menu.kind == .menuBar {
            let anchors = try within.map { try applications.readRoot($0) }
            guard anchors.allSatisfy({ $0.scope == read.scope && $0.application.isEqual(menu.owner) }) else {
                throw DriverError("selected menu ancestry belongs to a different scope", code: .staleTarget)
            }
            try op.check()
            if try NativeMenuUnit.membership(anchors.map(\.element) + [read.element], root: menu.element) == .confirmed {
                return
            }
        }
        let anchors = try within.map { ref in
            var anchored = binding
            anchored["node"] = ref
            _ = try target(anchored, input: true)
            return try applications.readRoot(ref).element
        }
        try checkNativeContainment(read.element, anchors: anchors, op: op)
    }

    func checkNativeContainment(_ leaf: AXUIElement, anchors: [AXUIElement], op: NativeOperation) throws {
        let evidence = try NativeContainment.check(
            leaf: leaf, within: anchors, same: { CFEqual($0, $1) },
            parent: { element in
                let value = AXAccess.read(element, kAXParentAttribute)
                if case .invalid = value { throw DriverError("path ancestor expired", code: .staleTarget) }
                guard let raw = value.value, CFGetTypeID(raw) == AXUIElementGetTypeID() else { return nil }
                return unsafeDowncast(raw, to: AXUIElement.self)
            },
            children: { element in
                var count: CFIndex = 0
                let status = AXUIElementGetAttributeValueCount(element, kAXChildrenAttribute as CFString, &count)
                if status == .invalidUIElement { throw DriverError("path ancestor expired", code: .staleTarget) }
                if [.attributeUnsupported, .notImplemented, .noValue].contains(status) { return [] }
                guard status == .success, count >= 0, count <= 2048 else { return nil }
                if count == 0 { return [] }
                var values: CFArray?
                guard
                    AXUIElementCopyAttributeValues(element, kAXChildrenAttribute as CFString, 0, count, &values)
                        == .success, let children = values as? [AXUIElement], children.count == count
                else { return nil }
                return children
            },
            check: {
                try op.check()
                try RequestDiagnostics.checkCancellation()
            })
        switch evidence {
        case .confirmed: return
        case .changed: throw DriverError("selected element left its logical path scope", code: .staleTarget)
        case .unavailable: throw DriverError("selected path containment cannot be confirmed", code: .inputUnavailable)
        }
    }

    func checkActuator(_ binding: [String: Any], actuator: NativeInspectQuery.Actuator?, op: NativeOperation) throws {
        guard let actuator else { return }
        if actuator.relation == "scrollPage" {
            try scrollUnit(binding, actuator: actuator).validate()
            return
        }
        if actuator.relation == "scrollPointer" {
            // Python selected the region and classified the exact hit path.
            // Check those fixed objects and containment, never choose another
            // ancestor or treat every descendant as a wheel receiver.
            guard let owner = binding["node"] as? String, let region = actuator.region else {
                throw DriverError(
                    "element scrolling requires an original target and selected region", code: .invalidRequest)
            }
            var objects = [String: AXUIElement]()
            for ref in Set([owner, region, actuator.node]) {
                var selected = binding
                selected["node"] = ref
                let current = try target(selected, input: true)
                guard current.blocked.isEmpty else {
                    throw DriverError("selected scrolling object is blocked by a modal", code: .blockedByModal)
                }
                let element = try applications.readRoot(ref).element
                // The content owner only locates this viewport. Permission
                // belongs to the selected wheel region and hit; owner aliases
                // of either receiver are therefore still checked here.
                if (ref == region || ref == actuator.node),
                    AXAccess.read(element, kAXEnabledAttribute).value as? Bool == false
                {
                    throw DriverError("selected scrolling object is disabled", code: .inputUnavailable)
                }
                objects[ref] = element
            }
            guard AXAccess.read(objects[region]!, kAXRoleAttribute).value as? String == kAXScrollAreaRole else {
                throw DriverError("selected wheel region is no longer a scroll area", code: .inputUnavailable)
            }
            for ref in Set([owner, actuator.node]) where ref != region {
                try checkNativeContainment(objects[ref]!, anchors: [objects[region]!], op: op)
            }
            return
        }
        guard let owner = binding["node"] as? String, owner != actuator.node else {
            throw DriverError("a selected control member requires a distinct requested owner", code: .invalidRequest)
        }
        _ = try target(binding, input: true)
        let scope = try inputScope(binding)
        let parent = try applications.readRoot(owner).element
        if AXAccess.read(parent, kAXEnabledAttribute).value as? Bool == false {
            throw DriverError("the requested control owner is disabled", code: .inputUnavailable)
        }
        var selected = binding
        selected["node"] = actuator.node
        let current = try target(selected, input: true)
        guard current.blocked.isEmpty else {
            throw DriverError(
                "the selected control member is blocked by modal \(current.blocked.joined(separator: ", "))",
                code: .blockedByModal)
        }
        let editor = try applications.readRoot(actuator.node).element
        let window = try scope.requireWindow()
        let anchors = CFEqual(window, parent) ? [parent] : [window, parent]
        try checkNativeContainment(editor, anchors: anchors, op: op)
    }

    func target(_ raw: Any?, canonicalize: Bool = false, input: Bool = false, attachedMenuAction: Bool = false) throws
        -> (wire: [String: Any], blocked: [String], node: String)
    {
        let requested = try NativeTarget(raw)
        try requested.requireSession(session)
        let node = try string(requested.node, label: "node")
        let read = try applications.readRoot(node)
        let scope = try inputScope(requested, canonicalize: canonicalize, observed: read)
        let value = scope.wire
        let surface = try string(value["surface"], label: "surface")
        guard read.scope == surface else {
            throw DriverError("node belongs to a different surface", code: .staleTarget)
        }
        try read.validate()
        if let menu = scope.menuRoot {
            // An application menu has no AXWindow. Its membership is proved
            // against the retained, freshly re-read AXMenuBar instead.
            if !CFEqual(read.element, menu),
                try NativeMenuUnit.membership(read.ancestors + [read.element], root: menu) != .confirmed
            {
                let reader = AXRelationReader()
                let evidence = try NativeContainment.check(
                    leaf: read.element, within: [menu], same: { CFEqual($0, $1) },
                    parent: { AXAccess.element($0, kAXParentAttribute) },
                    children: { try NativeMenuUnit.menuChildren($0, limit: 2048, reader: reader) },
                    check: { try RequestDiagnostics.checkCancellation() })
                guard evidence == .confirmed else {
                    diagnose(["kind": "application_menu_membership", "node": node,
                              "retainedAncestors": read.ancestors.count,
                              "result": evidence == .changed ? "changed" : "unavailable"])
                    throw DriverError(
                        "the selected item is no longer confirmed within the observed application menu",
                        code: evidence == .changed ? .staleTarget : .inputUnavailable)
                }
            }
        } else if input {
            // A retained object may move to another window without changing
            // its role, identifier or value. The old registry scope is not
            // evidence of its current input boundary.
            let expected = try scope.requireWindow()
            let actual = WindowGeometry.owningWindow(
                read.element, pid: scope.application.processIdentifier, in: expected)
            // Local menus can omit AXWindow and sit beyond the bounded generic
            // window search. An addressed action still has to prove every live
            // forward edge of its retained menu path; the registry scope alone
            // never authorizes input, and physical routes cannot use this proof.
            try NativeInputMembershipBoundary.validate(
                leaf: read.element, ancestors: read.ancestors, window: expected, currentWindow: actual,
                attachedMenuAction: attachedMenuAction, same: { CFEqual($0, $1) },
                role: { AXAccess.read($0, kAXRoleAttribute).value as? String },
                children: { element in
                    try RequestDiagnostics.checkCancellation()
                    guard let children = AXAccess.elements(element, kAXChildrenAttribute, limit: 2048) else {
                        throw DriverError("local menu attachment cannot be confirmed", code: .inputUnavailable)
                    }
                    return children
                })
        }
        return (value, scope.blocked, node)
    }

    func windowKeyboardQueryScope(_ raw: Any?) throws -> NativeInputScope? {
        try inputScope(raw)
    }

    func inputBinding(_ raw: Any?, input: Bool = false, attachedMenuAction: Bool = false) throws -> (
        wire: [String: Any], blocked: [String], node: String?
    ) {
        if let value = raw as? [String: Any], value["capture"] is String {
            let target = try NativeTarget(value)
            guard target.session == session, target.node == nil else {
                throw DriverError(
                    "visual input requires this session's exact window image binding", code: .invalidRequest)
            }
            if target.isDesktopImage {
                throw DriverError("desktop images locate points; use explicit Computer input", code: .invalidRequest)
            }
            let visual = try captures.inputScope(value)
            return (value, visual.blocked, nil)
        }
        if (raw as? [String: Any])?["node"] is NSNull {
            let scope = try inputScope(raw)
            return (scope.wire, scope.blocked, nil)
        }
        let value = try target(raw, input: input, attachedMenuAction: attachedMenuAction)
        return (value.wire, value.blocked, value.node)
    }

    /// The original field remains the operation target. Only its proved
    /// attached sheet may receive the unsent path suffix.
    func savePanelPathBoundary(_ raw: Any?, proof: NativeSavePanelPathContinuation.Proof) throws -> [String] {
        let requested = try NativeTarget(raw)
        try requested.requireSession(session)
        let original = try applications.readRoot(try string(requested.node, label: "node"))
        guard CFEqual(original.element, proof.original), original.application.processIdentifier == proof.presenter.pid
        else { throw DriverError("the SavePanel's original name field changed", code: .staleTarget) }
        let scope = try inputScope(requested, canonicalize: false, observed: original)
        try original.validate()
        guard let window = scope.window, CFEqual(window, proof.panel), let graph = scope.windowEvidence,
            NativeProcessIdentity.read(proof.presenter.pid) == proof.presenter,
            NativeProcessIdentity.read(proof.service.pid) == proof.service
        else { throw DriverError("the Go To sheet's original SavePanel scope changed", code: .staleTarget) }
        return try proof.blocked(
            in: graph.windows, root: string(requested.rootWindow, label: "rootWindow"),
            focused: applications.focusedWindow(scope.application, windows: graph.windows))
    }

    /// Only a selected Qt choice pointer consumes its two-path membership.
    /// Ordinary AX and generic pointer boundaries retain their own rules.
    func qtComboChoiceBoundary(_ raw: Any?, qualification proof: NativeQtComboChoice) throws -> [String] {
        let requested = try NativeTarget(raw)
        try requested.requireSession(session)
        if requested.capture != nil {
            guard requested.node == nil, let binding = raw as? [String: Any], proof.capturedPoint != nil else {
                throw DriverError("Qt captured choice requires its original pixel binding", code: .invalidRequest)
            }
            let input = try captures.input(binding)
            guard try proof.confirmsCaptured(input) else {
                throw DriverError("the original captured Qt choice point changed", code: .staleTarget)
            }
            return input.blocked
        }
        guard proof.capturedPoint == nil else {
            throw DriverError("Qt pixel proof cannot authorize an element intent", code: .invalidRequest)
        }
        let read = try applications.readRoot(try string(requested.node, label: "node"))
        guard read.application.processIdentifier == proof.pid, CFEqual(read.element, proof.element),
            let observed = NativeQtComboChoice.observationAnchor(
                logicalSurface: read.observedInteractionSurface, ancestors: read.ancestors,
                role: { AXAccess.read($0, kAXRoleAttribute).value as? String }),
            CFEqual(observed, proof.observedWindow),
            read.ancestors.count == proof.ancestors.count,
            zip(read.ancestors, proof.ancestors).allSatisfy({ CFEqual($0.0, $0.1) })
        else { throw DriverError("the selected Qt choice observation changed", code: .staleTarget) }
        let scope = try inputScope(requested, canonicalize: false, observed: read, popupOwner: proof.ownerStamp?.owner)
        try read.validate()
        guard try proof.confirms() else {
            throw DriverError("the original Qt choice popup membership changed", code: .staleTarget)
        }
        guard scope.blocked.isEmpty else { return scope.blocked }
        if !CFEqual(proof.observedWindow, proof.popup), let graph = scope.windowEvidence,
            NativePopupOwnerBoundary.hasKnownParentConflict(
                popup: proof.popup, owner: proof.observedWindow,
                windows: graph.windows.map {
                    .init(ref: $0.ref, element: $0.element, parent: $0.parent, modal: $0.modal,
                        parentUnknown: $0.parentUnknown)
                }, same: { CFEqual($0, $1) })
        {
            throw DriverError("Qt popup has a confirmed different window owner", code: .staleTarget)
        }
        if let owner = proof.ownerStamp?.owner {
            guard let graph = scope.windowEvidence else {
                throw DriverError("Qt popup owner boundary evidence unavailable", code: .inputUnavailable)
            }
            return try NativePopupOwnerBoundary.blocked(
                popup: proof.popup, owner: owner,
                windows: graph.windows.map { .init(ref: $0.ref, element: $0.element, parent: $0.parent, modal: $0.modal) },
                same: { CFEqual($0, $1) })
        }
        return []
    }

    /// Only NativeUnitBoundary's selected original-element HID contract can
    /// enter here. General target(input:true) keeps its original membership.
    func popupPointerBoundary(_ raw: Any?, qualification proof: NativePopupPointerQualification) throws -> [String] {
        let requested = try NativeTarget(raw)
        try requested.requireSession(session)
        let read = try applications.readRoot(try string(requested.node, label: "node"))
        guard read.application.processIdentifier == proof.pid, proof.context.process.pid == proof.pid else {
            throw DriverError("the popup pointer process changed", code: .staleTarget)
        }
        guard let original = read.popupOwner, original.matches(proof.context, same: { CFEqual($0, $1) }),
            CFEqual(read.element, proof.element)
        else { throw DriverError("the original popup observation changed", code: .staleTarget) }
        // A list uses its document as discovery hint. A split submenu is
        // already the working surface; its reverse branch owner is separate.
        let ownerHint = proof.context.kind == .list ? proof.context.owner : nil
        let scope = try inputScope(requested, canonicalize: false, observed: read, popupOwner: ownerHint)
        try read.validate()
        guard scope.blocked.isEmpty else { return scope.blocked }
        guard let graph = scope.windowEvidence else {
            throw DriverError("popup owner window evidence unavailable", code: .inputUnavailable)
        }
        let unit = NativeElementPointerUnit(
            application: scope.application, window: try scope.requireWindow(), element: read.element,
            logicalWindow: read.observedInteractionSurface)
        let current = WindowGeometry.owningWindow(
            read.element, pid: unit.application.processIdentifier, in: unit.window)
        let facts = NativePopupAXFacts(
            pid: unit.application.processIdentifier, until: RequestDiagnostics.deadline ?? .infinity,
            check: {
                try RequestDiagnostics.checkCancellation()
                if let until = RequestDiagnostics.deadline, ProcessInfo.processInfo.systemUptime >= until {
                    throw RequestDeadlineExceeded()
                }
            }).facts()
        return try NativePopupPointerBoundary.validate(
            proof, original: read.popupOwner, leaf: read.element, ancestors: read.ancestors, popup: unit.window,
            currentOwner: current, facts: facts, sample: { try unit.popupPhysicalSample(proof.geometry) },
            same: { CFEqual($0, $1) }, ownerBoundary: { popup, owner in
                return try NativePopupOwnerBoundary.blocked(
                    popup: popup, owner: owner,
                    windows: graph.windows.map { .init(ref: $0.ref, element: $0.element, parent: $0.parent, modal: $0.modal) },
                    same: { CFEqual($0, $1) })
            })
    }

    func popupPointerUnit(_ binding: [String: Any], qualification proof: NativePopupPointerQualification) throws
        -> any NativePopupPointerInput
    {
        let target = try NativeTarget(binding)
        try target.requireSession(session)
        let read = try applications.readRoot(try string(target.node, label: "node"))
        guard read.scope == target.surface, read.application.processIdentifier == proof.pid,
            CFEqual(read.element, proof.element), let observed = read.observedInteractionSurface,
            CFEqual(observed, proof.context.popup)
        else { throw DriverError("popup pointer prototype changed", code: .staleTarget) }
        // Construction follows the production final boundary and grants no
        // input. Delivery will re-enter that boundary before every OS event.
        let unit = NativeElementPointerUnit(
            application: read.application, window: proof.context.popup, element: read.element,
            logicalWindow: observed)
        return NativePopupPointerUnit(element: unit, qualification: proof)
    }

    func receiverRelation(
        _ binding: [String: Any], actuator: NativeInspectQuery.Actuator?, receiver: NativeInspectQuery.Receiver?
    ) throws -> NativeReceiverRelation? {
        guard let receiver else { return nil }
        let editor = try actuator?.node ?? string(binding["node"], label: "node")
        guard receiver.reference != editor else {
            throw DriverError("related receiver must differ from its control", code: .invalidRequest)
        }
        var related = binding
        related["node"] = receiver.reference
        _ = try inputBinding(related)
        return NativeReceiverRelation(
            editor: try applications.readRoot(editor).element,
            related: try applications.readRoot(receiver.reference).element, selection: receiver)
    }

    func actionContextUnit(_ binding: [String: Any], action: String) throws -> (any NativeApplicationContextInput)? {
        guard action == kAXPressAction || action == kAXShowMenuAction else { return nil }
        let scope = try inputScope(binding)
        guard NativeObjectActionPolicy.applicationContextEligible(
            action: action, hasWindow: scope.window != nil, applicationMenu: scope.menuRoot != nil)
        else { return nil }
        // The original registry/provider attachment establishes this host;
        // AppKit target/action activity is not specific to Web content or role.
        let node = try string(binding["node"], label: "node")
        _ = try applications.readRoot(node)
        return NativeApplicationContextUnit(application: scope.application)
    }

    func applicationKeyboardUnit(_ binding: [String: Any]) throws -> NativeApplicationKeyboardUnit? {
        guard binding["node"] is NSNull, binding["rootWindow"] is NSNull, binding["capture"] is NSNull else {
            return nil
        }
        let scope = try inputScope(binding)
        guard scope.window == nil, let menu = scope.menuRoot else { return nil }
        return .init(application: scope.application, menu: menu)
    }

    func keyboardUnit(
        _ binding: [String: Any], actuator: NativeInspectQuery.Actuator? = nil,
        receiver: NativeInspectQuery.Receiver? = nil
    ) throws -> NativeKeyboardUnit {
        let scope = try inputScope(binding)
        let node = actuator?.node ?? (binding["node"] as? String)
        let window = try scope.requireWindow()
        let read = try node.map { try applications.readRoot($0) }
        let owned = try read.flatMap { original in
            try original.cellEditorState?.resolve(
                element: original.element, application: scope.application, window: window)
        }
        return NativeKeyboardUnit(
            application: scope.application, window: window,
            element: owned?.element ?? read?.element,
            receiverRelation: try receiverRelation(binding, actuator: actuator, receiver: receiver),
            cellEditorState: read?.cellEditorState, cellEditor: owned?.proof,
            windowCommandScope: binding["node"] is NSNull,
            boundaryCoverageComplete: scope.windowEvidence?.coverageComplete == true)
    }

    func selectedTextUnit(
        _ binding: [String: Any], actuator: NativeInspectQuery.Actuator?, receiver: NativeInspectQuery.Receiver? = nil
    ) throws -> NativeSelectedTextUnit {
        let scope = try inputScope(binding)
        let node = try actuator?.node ?? string(binding["node"], label: "node")
        let window = try scope.requireWindow()
        let read = try applications.readRoot(node)
        let owned = try read.cellEditorState?.resolve(
            element: read.element, application: scope.application, window: window)
        return NativeSelectedTextUnit(
            application: scope.application, window: window,
            element: owned?.element ?? read.element,
            receiverRelation: try receiverRelation(binding, actuator: actuator, receiver: receiver),
            cellEditor: owned?.proof)
    }

    func elementPointerUnit(_ binding: [String: Any], actuator: NativeInspectQuery.Actuator? = nil) throws
        -> NativeElementPointerUnit
    {
        let scope = try inputScope(binding)
        let node = try string(binding["node"], label: "node")
        let scrolling = actuator?.relation == "scrollPointer"
        let geometry = scrolling ? actuator!.region! : node
        let read = try applications.readRoot(geometry)
        // Use the same original-observation anchor as Qt offer sampling and
        // final validation; generic nested-surface absence cannot erase it.
        let combo = try actuator == nil ? NativeQtComboChoice.query(
            element: read.element, role: AXAccess.read(read.element, kAXRoleAttribute).value as? String,
            observedWindow: read.observedInteractionSurface, ancestors: read.ancestors,
            pid: scope.application.processIdentifier) : nil
        return NativeElementPointerUnit(
            application: scope.application, window: try combo?.popup ?? scope.requireWindow(),
            element: read.element,
            member: try actuator.map { try applications.readRoot($0.node).element }, kind: scrolling ? .scroll : .click,
            logicalWindow: read.observedInteractionSurface, qtComboChoice: combo)
    }

    func menuUnit(_ binding: [String: Any], within: [String] = []) throws -> NativeMenuUnit {
        let scope = try inputScope(binding)
        guard scope.menuRoot != nil, let node = binding["node"] as? String else {
            throw DriverError("menu unit requires an application-menu element", code: .invalidRequest)
        }
        return NativeMenuUnit(
            application: scope.application, window: scope.window, element: try applications.readRoot(node).element,
            root: scope.menuRoot!, path: try (within + [node]).map { try applications.readRoot($0).element },
            attachedDialogs: scope.window.flatMap {
                NativeMenuAttachedDialogs.candidate(root: $0, evidence: scope.windowEvidence,
                                                    pid: scope.application.processIdentifier)
            })
    }

    func scrollUnit(_ binding: [String: Any], actuator: NativeInspectQuery.Actuator) throws -> NativeScrollUnit {
        guard actuator.relation == "scrollPage", let region = actuator.region, let bar = actuator.bar,
            let requested = binding["node"] as? String
        else { throw DriverError("scrolling requires a selected native page route", code: .invalidRequest) }
        func object(_ ref: String) throws -> AXUIElement {
            var selected = binding
            selected["node"] = ref
            let current = try target(selected, input: true)
            guard current.blocked.isEmpty else {
                throw DriverError("scrolling target is blocked by a modal", code: .blockedByModal)
            }
            return try applications.readRoot(ref).element
        }
        return NativeScrollUnit(
            requested: try object(requested), region: try object(region), bar: try object(bar),
            button: try object(actuator.node))
    }

    func same(_ a: [String: Any], _ b: [String: Any]) -> Bool { NSDictionary(dictionary: a).isEqual(to: b) }

    func directoryTarget(_ raw: Any?) throws -> [String: Any]? {
        let value = try NativeTarget(raw)
        try value.requireSession(session)
        return value.isDiscovery ? value.wire : nil
    }

    func windowTarget(_ raw: Any?) throws -> [String: Any] {
        var value = try NativeTarget(raw)
        guard value.session == session, value.node == nil, value.capture == nil else {
            throw DriverError(
                "window capture requires this session's window binding, without an element or old image",
                code: .invalidRequest)
        }
        let resolved = try directory.windowScope(
            instance: string(value.instance, label: "instance"), root: value.rootWindow,
            surface: string(value.surface, label: "surface"))
        value.rootWindow = resolved.root
        return value.wire
    }

    func observationTarget(_ raw: Any?) throws -> (wire: [String: Any], node: String) {
        var value = try NativeTarget(raw)
        let node = try string(value.node, label: "node")
        var window = value
        window.node = nil
        let scope = try readScope(window.wire)
        let observed = try applications.nativeReadTarget(node)
        let surface = try string(scope.wire["surface"], label: "surface")
        guard observed.scope == surface, observed.application.isEqual(scope.owner) else {
            throw DriverError("observed node belongs to another UI scope", code: .staleTarget)
        }
        if directory.applicationRoot(surface)?.kind == .applicationContent {
            _ = try directory.applicationRootScope(
                instance: string(value.instance, label: "instance"), root: value.rootWindow,
                surface: surface, containing: observed.element)
        }
        value.rootWindow = scope.wire["rootWindow"] as? String
        return (value.wire, node)
    }

    func readScope(_ raw: Any?) throws -> (wire: [String: Any], owner: NSRunningApplication) {
        let value = try NativeTarget(raw)
        guard value.session == session, value.node == nil, value.capture == nil else {
            throw DriverError("UI-root observation requires this session's read binding", code: .invalidRequest)
        }
        if let appRoot = try directory.applicationRootScope(
            instance: string(value.instance, label: "instance"), root: value.rootWindow,
            surface: string(value.surface, label: "surface"))
        {
            return (value.wire, appRoot.owner)
        }
        let window = try windowTarget(value.wire)
        guard let surface = directory.surface(try string(window["surface"], label: "surface")) else {
            throw DriverError("observed window scope is unavailable", code: .staleTarget)
        }
        return (window, surface.owner)
    }
}
