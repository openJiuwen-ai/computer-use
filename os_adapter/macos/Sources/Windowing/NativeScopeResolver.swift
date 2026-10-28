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

    private func inputScope(_ target: NativeTarget, canonicalize: Bool, observed: NativeApplicationReader.ReadRoot?)
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
            if let root = entry.root {
                let windows = try applications.windows(
                    entry.owner, nativeWindows: NativeWindows.list(entry.owner),
                    contentRoot: directory.windowElement(root, owner: entry.owner), purpose: .inputValidation)
                guard let window = windows.first(where: { $0.ref == root })?.element else {
                    throw DriverError("the menu's original working window closed", code: .staleTarget)
                }
                let blocked = try NativeWindowBoundary.blocked(
                    root: root, target: root,
                    windows: windows.map { .init(ref: $0.ref, parent: $0.parent, modal: $0.modal == true) },
                    focused: applications.focusedWindow(entry.owner, windows: windows))
                return NativeInputScope(
                    wire: value.wire, application: entry.owner, window: window, menuRoot: menu.element, blocked: blocked
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
        let contentRoot = try workingRoot.map { original in
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
        var windows = try applications.windows(
            entry.owner, nativeWindows: native, contentRoot: contentRoot, purpose: .inputValidation)
        // The hint cannot replace proof that the original App root is live.
        // It may itself be a deep popover omitted by the narrower scan.
        if let original = workingRoot, let contentRoot, !CFEqual(original, contentRoot),
            !windows.contains(where: { $0.ref == root })
        {
            windows = try applications.windows(entry.owner, nativeWindows: native, purpose: .inputValidation)
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
                wire: value.wire, application: entry.owner, window: popup, menuRoot: nil, blocked: blocked)
        }
        return NativeInputScope(
            wire: value.wire, application: entry.owner, window: nativeWindow, menuRoot: nil, blocked: blocked)
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
        let projection = try applications.keyboardBoundary(anchor.application, contentRoot: anchor.window)
        let decision = NativeKeyboardBoundaryPolicy.decide(
            original: anchor.window, projection: projection, same: { CFEqual($0, $1) })
        if decision == .confirmed, let root = projection.windows.first(where: { CFEqual($0.element, anchor.window) }) {
            // Same boundary algorithm, with sample-private keys. No modal can
            // enter this branch: all known modal results require the original Gate.
            let blocked = try NativeWindowBoundary.blocked(
                root: root.key, target: root.key,
                windows: projection.windows.map { .init(ref: $0.key, parent: $0.parent, modal: $0.modal == true) })
            if !blocked.isEmpty { return .needsLegacy("live modal requires full scope") }
        }
        return decision
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
        let leaf = try applications.readRoot(node).element
        let anchors = try within.map { ref in
            var anchored = binding
            anchored["node"] = ref
            _ = try target(anchored, input: true)
            return try applications.readRoot(ref).element
        }
        try checkNativeContainment(leaf, anchors: anchors, op: op)
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
                if AXAccess.read(element, kAXEnabledAttribute).value as? Bool == false {
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
            if !CFEqual(read.element, menu) {
                let evidence = try NativeContainment.check(
                    leaf: read.element, within: [menu], same: { CFEqual($0, $1) },
                    parent: { AXAccess.element($0, kAXParentAttribute) },
                    children: { AXAccess.elements($0, kAXChildrenAttribute, limit: 2048) },
                    check: { try RequestDiagnostics.checkCancellation() })
                guard evidence == .confirmed else {
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
            guard
                let actual = WindowGeometry.owningWindow(
                    read.element, pid: scope.application.processIdentifier, in: expected)
            else {
                throw DriverError("native target's current window membership is unavailable", code: .inputUnavailable)
            }
            let localMenu =
                try attachedMenuAction && !CFEqual(actual, expected)
                && LocalMenuAttachment.confirms(
                    leaf: read.element, ancestors: read.ancestors, window: expected, same: { CFEqual($0, $1) },
                    role: { AXAccess.read($0, kAXRoleAttribute).value as? String },
                    children: { element in
                        try RequestDiagnostics.checkCancellation()
                        guard let children = AXAccess.elements(element, kAXChildrenAttribute, limit: 2048) else {
                            throw DriverError("local menu attachment cannot be confirmed", code: .inputUnavailable)
                        }
                        return children
                    })
            guard CFEqual(actual, expected) || localMenu else {
                throw DriverError("native target moved outside its observed interaction surface", code: .staleTarget)
            }
        }
        return (value, scope.blocked, node)
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
                guard target.instance == nil, target.rootWindow == nil else {
                    throw DriverError(
                        "desktop input cannot contain application/window identities", code: .invalidRequest)
                }
                try desktopUnit(value).check()
                return (value, [], nil)
            }
            let visual = try captures.input(value)
            return (value, visual.blocked, nil)
        }
        if (raw as? [String: Any])?["node"] is NSNull {
            let scope = try inputScope(raw)
            return (scope.wire, scope.blocked, nil)
        }
        let value = try target(raw, input: input, attachedMenuAction: attachedMenuAction)
        return (value.wire, value.blocked, value.node)
    }

    func isDesktop(_ binding: [String: Any]) -> Bool { binding["surface"] is NSNull && binding["capture"] is String }

    func desktopUnit(_ binding: [String: Any]) throws -> NativeDesktopUnit {
        NativeDesktopUnit(desktop: desktop(), ref: try string(binding["capture"], label: "capture"))
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
            windowCommandScope: binding["node"] is NSNull)
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
        return NativeElementPointerUnit(
            application: scope.application, window: try scope.requireWindow(),
            element: try applications.readRoot(geometry).element,
            member: try actuator.map { try applications.readRoot($0.node).element }, kind: scrolling ? .scroll : .click,
            logicalWindow: try applications.readRoot(geometry).observedInteractionSurface)
    }

    func menuUnit(_ binding: [String: Any], within: [String] = []) throws -> NativeMenuUnit {
        let scope = try inputScope(binding)
        guard scope.menuRoot != nil, let node = binding["node"] as? String else {
            throw DriverError("menu unit requires an application-menu element", code: .invalidRequest)
        }
        return NativeMenuUnit(
            application: scope.application, window: scope.window, element: try applications.readRoot(node).element,
            root: scope.menuRoot!, path: try (within + [node]).map { try applications.readRoot($0).element })
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
