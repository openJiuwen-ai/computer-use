// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices

/// Discovery and retained scope identities. Selection belongs to Python; this
/// collector never picks a document, launches, activates, or reads its contents.
@MainActor final class NativeDirectory {
    struct Surface {
        enum QueryHint {
            case observedModal(isSheet: Bool)
            case retainedModal(NativeApplicationReader.ModalIdentity)
        }
        let owner: NSRunningApplication
        let window: String
        let scope: String
        let parent: String?
        let element: AXUIElement?
        let native: NativeWindows.Window?
        // Historical evidence selects a query; it never proves current
        // membership, ownership, or modality. Keep it with this identity.
        var queryHint: QueryHint? = nil
        var contentHost: String? = nil
    }
    private let state: NativeDesktopState
    private let session: String
    /// Native window adapters share this directory's retained identities. The
    /// defaults are live OS queries; tests replace only these leaf adapters.
    @MainActor struct WindowServices {
        var locate: ((String) throws -> NSRunningApplication)? = nil
        var listIfAvailable: (NSRunningApplication) -> [NativeWindows.Window]? = { NativeWindows.listIfAvailable($0) }
        var trusted: () -> Bool = AXIsProcessTrusted
        var activate: ((NSRunningApplication) -> AccessibilityActivation.Outcome)? = nil
        var focusedWindow: ((NSRunningApplication) -> AXUIElement?)? = nil
        var registerRoot: ((AXUIElement, NSRunningApplication, String) throws -> String)? = nil
        var observedNode: ((AXUIElement, NSRunningApplication, String) -> String?)? = nil
        var list: (NSRunningApplication) -> [NativeWindows.Window] = { NativeWindows.list($0) }
        var owner: (NSRunningApplication) throws -> Void = { try BackgroundTarget($0).validate() }
        var process: (pid_t) -> NativeProcessIdentity? = { NativeProcessIdentity.read($0) }
        var info: (CGWindowID) -> [String: Any]? = { WindowServerInfo.window($0) }
        var presence: (CGWindowID) -> WindowServerInfo.Presence = { WindowServerInfo.read($0) }
        var onScreenWindows: () -> Set<CGWindowID>? = { WindowServerInfo.onScreenWindows() }
        var parents: ([CGWindowID]) -> [CGWindowID: CGWindowID]? = { NativeWindowParents.read($0) }
        var isGone: (pid_t) -> Bool = { NativeProcessIdentity.isGone($0) }
        var role: (AXUIElement) -> String? = { AXAccess.read($0, kAXRoleAttribute).value as? String }
    }
    private let windowServices: WindowServices
    private var surfaces = [String: Surface]()
    private var endedWindows = [String: (owner: NSRunningApplication, value: [String: Any])]()
    struct ApplicationRoot {
        enum Kind: String { case menuBar, applicationContent }
        let owner: NSRunningApplication
        let element: AXUIElement
        let scope: String
        let kind: Kind
    }
    private var applicationRoots = [String: ApplicationRoot]()
    private struct InstallationEntry {
        let name: String
        let identity: NativeInstallation?
        let failure: String?
    }
    private var installations = [String: InstallationEntry]()
    private var installationIDs = [NativeInstallation: String]()
    private var installationOrder = [String]()

    init(state: NativeDesktopState, session: String, windowServices: WindowServices = WindowServices()) {
        self.state = state
        self.session = session
        self.windowServices = windowServices
    }
    func surface(_ id: String) -> Surface? { surfaces[id] }

    /// This only carries optional foreground responsibility to a related call.
    /// It neither grants input membership nor samples/changes desktop state.
    func sameInteraction(_ first: [String: Any], _ second: [String: Any]) -> Bool {
        guard let a = first["surface"] as? String, let b = second["surface"] as? String else { return false }
        if a == b { return first["instance"] as? String == second["instance"] as? String }
        func context(_ scope: String, _ wire: [String: Any]) -> (NSRunningApplication, Set<String>)? {
            guard let owner = surfaces[scope]?.owner ?? applicationRoots[scope]?.owner else { return nil }
            var pending = [(wire["rootWindow"] as? String) ?? surfaces[scope]?.window].compactMap { $0 }
            var seen = Set<String>()
            while let key = pending.popLast() {
                guard seen.insert(key).inserted else { continue }
                if let item = surfaces.values.first(where: { $0.owner.isEqual(owner) && $0.window == key }) {
                    pending += [item.parent, item.contentHost].compactMap { $0 }
                }
            }
            return (owner, seen)
        }
        guard let left = context(a, first), let right = context(b, second), left.0.isEqual(right.0) else { return false }
        return !left.1.isDisjoint(with: right.1)
    }

    /// A desktop row is a direct native window selection, independent of AX
    /// completeness or parent relationships. Reuse the same identity owners
    /// as application discovery; presentation never substitutes title/frame.
    /// CGWindowNumber is unique within the user session (Apple Quartz Window
    /// Services); the registry additionally binds the process incarnation.
    func retainDesktopWindow(_ native: NativeWindows.Window, owner: NSRunningApplication) -> [String: Any] {
        let app = owner
        let window = state.applications.windowReference(native)
        let scope = state.applications.observationScope(app, window: nil, reference: window)
        let instance = state.appSelections.get(owner: app, root: window)
        let previous = surfaces[scope]
        let same = previous?.owner.isEqual(app) == true && previous?.window == window
            && previous?.native?.process == native.process && previous?.native?.mapping.id == native.mapping.id
        // Preserve retained AX and relation evidence for the original target's
        // input-boundary queries. This catalogue makes no new AX/modal claim.
        remember(Surface(owner: app, window: window, scope: scope,
            parent: same ? previous?.parent : nil, element: same ? previous?.element : nil, native: native,
            contentHost: same ? previous?.contentHost : nil))
        return [
            "window": window, "instance": instance.id, "surface": scope, "node": NSNull(),
            "title": native.mapping.title ?? "", "kind": "window", "parent": NSNull(),
            "parentState": "unknown", "modal": NSNull(), "nativeWindow": true, "accessibility": false
        ]
    }

    func windowElement(_ window: String, owner: NSRunningApplication) -> AXUIElement? {
        surfaces.values.first { $0.window == window && $0.owner.isEqual(owner) }?.element
    }
    func applicationRoot(_ id: String) -> ApplicationRoot? { applicationRoots[id] }

    private func changeStatus(_ owner: NSRunningApplication, application: String,
                              arm: Bool = true) throws -> NativeAppObserver.Status {
        guard windowServices.trusted() else { return .init(stamp: nil, notifications: []) }
        return try state.appObserver.status(owner: owner, application: application, session: session,
            expected: windowServices.process(owner.processIdentifier), arm: arm)
    }

    func appChangeBaseline(_ binding: [String: Any]) throws -> NativeAppObserver.Stamp? {
        guard let instance = binding["instance"] as? String else { return nil }
        let owner = try state.appSelections.entry(instance).owner
        guard let application = state.discovery.selector(for: owner) else { return nil }
        return try changeStatus(owner, application: application).stamp
    }

    func latestAppChange(_ armed: NativeAppObserver.Stamp) -> NativeAppObserver.Stamp? {
        state.appObserver.latest(armed)
    }

    func windowScopeEpoch(_ owner: NSRunningApplication) -> (incarnation: String, epoch: UInt64)? {
        state.appObserver.windowScopeEpoch(owner: owner, session: session)
    }

    private func changeOwner(_ application: String) throws -> NSRunningApplication {
        guard ApplicationInstances<NSRunningApplication>.isInstanceSelector(application),
            case .running(let owner) = try state.discovery.locate(application) else {
            throw DriverError("App change queries require an observed running-application id", code: .invalidRequest)
        }
        return owner
    }

    func appChanges(_ application: String) throws -> [String: Any] {
        let status = try changeStatus(changeOwner(application), application: application)
        return ["kind": "appChanges", "session": session, "application": application,
            "incarnation": status.stamp?.incarnation as Any? ?? NSNull(),
            "token": status.stamp?.token as Any? ?? NSNull(), "available": status.available,
            "notifications": status.notifications]
    }

    func waitAppChange(_ application: String, after: String, timeoutMs: Int) async throws -> [String: Any] {
        let owner = try changeOwner(application)
        let status = try changeStatus(owner, application: application)
        let change: NativeAppObserver.Change
        if status.available {
            change = try await state.appObserver.wait(owner: owner, application: application, session: session,
                after: after, timeoutMs: timeoutMs)
        } else { change = .init(stamp: nil, reason: "unavailable", events: [], elapsedMs: 0) }
        try windowServices.owner(owner)
        try RequestDiagnostics.checkCancellation()
        return ["kind": "waitAppChange", "session": session, "application": application,
            "incarnation": change.stamp?.incarnation as Any? ?? NSNull(),
            "token": change.stamp?.token as Any? ?? NSNull(), "reason": change.reason,
            "events": change.events, "elapsedMs": change.elapsedMs]
    }

    /// Application UI such as the menu bar is not a document window. When a
    /// caller has selected a document, keep that binding and its lifetime; a
    /// read of a global root cannot revive a closed working window.
    func applicationRootScope(
        instance: String, root: String?, surface: String, containing: AXUIElement? = nil
    ) throws -> ApplicationRoot? {
        guard let selected = applicationRoots[surface] else { return nil }
        let entry = try state.appSelections.entry(instance)
        guard entry.owner.isEqual(selected.owner), entry.root == root else {
            throw DriverError("application root belongs to another application or working window", code: .staleTarget)
        }
        try BackgroundTarget(selected.owner).validate()
        if let root {
            guard let window = surfaces.values.first(where: { $0.owner.isEqual(selected.owner) && $0.window == root })
            else { throw DriverError("the original working window is no longer retained", code: .staleTarget) }
            try validateWindow(window)
        }
        let app = AXUIElementCreateApplication(selected.owner.processIdentifier)
        AXMessaging.configure(app)
        switch selected.kind {
        case .menuBar:
            guard let current = AXAccess.element(app, kAXMenuBarAttribute), CFEqual(current, selected.element) else {
                throw DriverError("the observed application menu root changed or is unavailable", code: .staleTarget)
            }
        case .applicationContent:
            guard root == nil, CFEqual(app, selected.element) else {
                throw DriverError("the observed application content root changed", code: .staleTarget)
            }
            guard let containing else { return selected }
            switch try applicationContent(app, owner: selected.owner, containing: containing) {
            case .confirmed: break
            case .absent:
                throw DriverError("the application content scope is no longer present", code: .staleTarget)
            case .incomplete:
                throw DriverError("the application content scope could not be confirmed", code: .observationIncomplete)
            }
        }
        return selected
    }

    private func applicationContent(
        _ application: AXUIElement, owner: NSRunningApplication, containing: AXUIElement? = nil
    ) throws -> NativeApplicationContent.Evidence {
        let deadline = min(ProcessInfo.processInfo.systemUptime + 0.2, RequestDiagnostics.deadline ?? .infinity)
        func withinBudget() throws -> Bool {
            try RequestDiagnostics.checkCancellation()
            return ProcessInfo.processInfo.systemUptime < deadline
        }
        func withBoundedTimeout<T>(_ element: AXUIElement, fallback: T, _ query: () throws -> T) rethrows -> T {
            let remaining = deadline - ProcessInfo.processInfo.systemUptime
            guard AXUIElementSetMessagingTimeout(element, Float(max(0.001, min(0.05, remaining)))) == .success else {
                return fallback
            }
            defer { AXMessaging.configure(element) }
            return try query()
        }
        return try NativeApplicationContent.evidence(
            application, pid: owner.processIdentifier, containing: containing,
            role: { element in
                withBoundedTimeout(element, fallback: nil) {
                    AXAccess.read(element, kAXRoleAttribute).value as? String
                }
            },
            elements: { element, attribute, limit in
                try withBoundedTimeout(element, fallback: .unavailable(.failure)) {
                    try NativeApplicationContent.array(element, attribute, limit: limit, withinBudget: withinBudget)
                }
            },
            owner: { element in
                var pid: pid_t = 0
                return AXUIElementGetPid(element, &pid) == .success ? pid : nil
            },
            withinBudget: withinBudget)
    }

    func collectRoots(_ application: String, applicationContent requestedContent: Bool = false) throws -> [String: Any] {
        guard ApplicationInstances<NSRunningApplication>.isInstanceSelector(application),
            case .running(let app) = try state.discovery.locate(application)
        else { throw DriverError("root discovery requires an observed running-application id", code: .invalidRequest) }
        let began = Int64(Date().timeIntervalSince1970 * 1000)
        let appChange = try changeStatus(app, application: application).stamp
        var roots = [[String: Any]](), reasons = [String]()
        if AXIsProcessTrusted() {
            _ = state.accessibilityActivation.ensure(app)
            let element = AXUIElementCreateApplication(app.processIdentifier)
            AXMessaging.configure(element)
            func retain(_ root: AXUIElement, kind: ApplicationRoot.Kind) throws {
                let scope = state.applications.observationScope(app, window: kind == .menuBar ? nil : root)
                let node = try state.applications.registerNativeRoot(root, app: app, scope: scope)
                let instance = state.appSelections.get(owner: app, root: nil)
                applicationRoots[scope] = ApplicationRoot(owner: app, element: root, scope: scope, kind: kind)
                roots.append(["kind": kind.rawValue, "instance": instance.id, "surface": scope, "node": node])
            }
            if requestedContent {
                switch try applicationContent(element, owner: app) {
                case .confirmed: try retain(element, kind: .applicationContent)
                case .absent: break
                case .incomplete: reasons.append("application content root could not be confirmed")
                }
            } else {
                let read = AXAccess.read(element, kAXMenuBarAttribute)
                switch read {
                case .value(let raw) where CFGetTypeID(raw) == AXUIElementGetTypeID():
                    try retain(unsafeDowncast(raw, to: AXUIElement.self), kind: .menuBar)
                case .unsupported, .unavailable(.noValue): break
                default: reasons.append("application menu root could not be read")
                }
            }
        } else {
            reasons.append("accessibility permission unavailable")
        }
        try BackgroundTarget(app).validate()
        try RequestDiagnostics.checkCancellation()
        var value = result("roots", began: began, reasons: reasons)
        value["application"] = application
        value["roots"] = roots
        if let appChange { value["appChange"] = appChange.wire }
        return value
    }

    func installation(_ raw: Any?) throws -> (wire: [String: Any], identity: NativeInstallation, name: String) {
        let p = try object(raw, keys: ["session", "application"])
        guard try string(p["session"], label: "session") == session else {
            throw DriverError("installation belongs to another session", code: .staleTarget)
        }
        let id = try string(p["application"], label: "application")
        guard let entry = installations[id] else {
            throw DriverError("installation was not observed in this driver session or expired", code: .staleTarget)
        }
        guard let identity = entry.identity else {
            throw DriverError(entry.failure ?? "installation identity is unavailable", code: .observationIncomplete)
        }
        try identity.validate()
        return (p, identity, entry.name)
    }

    private func retainInstallation(_ item: [String: Any]) -> [String: Any] {
        var value = item
        let identity: NativeInstallation?, issue: String?
        do {
            guard let path = item["path"] as? String else {
                throw DriverError("installation path is unavailable", code: .observationIncomplete)
            }
            identity = try NativeInstallation(path: path)
            issue = nil
        } catch {
            identity = nil
            issue = String(describing: error)
        }
        let id = identity.flatMap { installationIDs[$0] } ?? "installation-\(UUID().uuidString)"
        installations[id] = InstallationEntry(
            name: item["displayName"] as? String ?? "application", identity: identity, failure: issue)
        if let identity { installationIDs[identity] = id }
        installationOrder.removeAll { $0 == id }
        installationOrder.append(id)
        while installationOrder.count > 4096 {
            if let expired = installations.removeValue(forKey: installationOrder.removeFirst()),
                let identity = expired.identity
            {
                installationIDs.removeValue(forKey: identity)
            }
        }
        value["id"] = id
        return value
    }

    /// Read-only range validation uses the directory's native window identity,
    /// not a tree node or input recipient. It does not choose a different view.
    func windowScope(instance: String, root: String?, surface id: String) throws -> (root: String, surface: Surface) {
        let entry = try state.appSelections.entry(instance)
        guard let expected = entry.root, root == nil || root == expected, let selected = surfaces[id],
            selected.owner.isEqual(entry.owner),
            let selectedRoot = surfaces.values.first(where: { $0.owner.isEqual(entry.owner) && $0.window == expected })
        else { throw DriverError("window capture has no retained working-window binding", code: .staleTarget) }
        var current = selected, visited = Set<String>()
        while current.window != expected {
            guard visited.insert(current.window).inserted, let parent = current.parent,
                let ancestor = surfaces.values.first(where: { $0.owner.isEqual(entry.owner) && $0.window == parent })
            else {
                throw DriverError("capture surface is outside its observed working-window range", code: .staleTarget)
            }
            current = ancestor
        }
        // A closed root never turns an existing App into another document.
        try validateWindow(selectedRoot)
        if selected.window != expected { try validateWindow(selected) }
        return (expected, selected)
    }

    func validateWindow(_ surface: Surface) throws {
        try windowServices.owner(surface.owner)
        if let native = surface.native {
            guard windowServices.process(native.process.pid) == native.process,
                let window = windowServices.info(native.mapping.id),
                (window[kCGWindowOwnerPID as String] as? NSNumber)?.int32Value == native.process.pid
            else { throw DriverError("observed native window closed or its process changed", code: .staleTarget) }
            return
        }
        // AX-only surfaces have no independent WindowServer identity. Query the
        // exact retained object, without requiring its label, children or focus.
        guard let element = surface.element, windowServices.role(element) != nil else {
            throw DriverError("observed window is no longer available", code: .staleTarget)
        }
    }

    /// Visual input keeps native window identity independent of AX content.
    /// Re-read known modal/containment relations; an old capture's parent edge
    /// cannot by itself authorize input into a formerly attached child.
    func inputWindowScope(instance: String, root: String, surface id: String) throws -> (
        surface: Surface, blocked: [String]
    ) {
        let selected = try windowScope(instance: instance, root: root, surface: id).surface
        let metadata = { ["rootWindow": root, "surface": id] as [String: Any] }
        let native = NativePointerTiming.measure("captureScope.nativeWindows", metadata: metadata) {
            windowServices.list(selected.owner)
        }
        let modalHints = try retainedModalHints(owner: selected.owner, native: native)
        let necessaryHints = modalHints.contains {
            $0.nativePresence != .ended && $0.identity.window != selected.window
        }
        let boundary: NativeApplicationReader.InputBoundary
        do {
            boundary = try NativePointerTiming.measure("captureScope.accessibilityWindows", metadata: metadata) {
                let original = windowElement(root, owner: selected.owner)
                // The same collector samples live boundary facts first. Only
                // a required root/surface missing from that frontier needs its
                // existing content-discovery fallback. No retained edge proves
                // current modality or membership by itself.
                return try state.applications.inputBoundary(
                    selected.owner, nativeWindows: native, contentRoot: windowElement(root, owner: selected.owner),
                    required: [original, selected.element].compactMap { $0 },
                    modalHints: modalHints)
            }
        } catch {
            try RequestDiagnostics.checkCancellation()
            // A directly selected native window is still an addressable
            // visual scope. Unknown AX data is not proof that it is closed.
            guard selected.window == root, selected.native != nil, !necessaryHints else { throw error }
            return (selected, [])
        }
        let windows = boundary.windows
        let inputWindows = windows.map { AppSelection.Window(ref: $0.ref, parent: $0.parent, modal: $0.modal == true) }
            + nativeInputAttachments(boundary, native: native)
        let unknownParents = Set(windows.filter(\.parentUnknown).map(\.ref))
        if selected.native == nil {
            // An AX-only surface has no independent physical-window identity
            // to retain the visual route when provider membership disappears.
            guard let expected = selected.element,
                windows.contains(where: { $0.ref == selected.window && CFEqual($0.element, expected) })
            else { throw DriverError("the bound AX-only surface cannot be found in its live scope", code: .inputUnavailable) }
        }
        var path = Set<String>(), current: String? = selected.window
        while let ref = current, path.insert(ref).inserted {
            if ref == root { break }
            guard let row = inputWindows.first(where: { $0.ref == ref }), !unknownParents.contains(ref) else {
                throw DriverError("current input-surface ownership cannot be confirmed", code: .inputUnavailable)
            }
            current = row.parent
        }
        guard path.contains(root) else {
            throw DriverError("input surface left its selected working-window range", code: .staleTarget)
        }
        // A modal is not its own blocker, nor the blocker of an input surface
        // whose current path already proves that modal is its ancestor. Keep
        // ordinary native-root visual degradation independent of AX details.
        let requiredModalHints = boundary.unresolvedModalHints.subtracting(
            unrelatedModalHints(boundary, hints: modalHints, native: native, root: root))
        guard requiredModalHints.isSubset(of: path) else {
            throw DriverError("a previously observed modal surface cannot be revalidated", code: .inputUnavailable)
        }
        // A positively observed modal/sheet with an unresolved owner is a
        // critical unknown, not evidence that it is an unrelated non-modal.
        // Incomplete ordinary control contents are not a global input blocker.
        let unresolvedModal = windows.contains { modal in
            guard !path.contains(modal.ref), modal.modal == true || (modal.kind == "sheet" && modal.modal != false)
            else { return false }
            if modal.kind == "sheet" && modal.parent == nil { return true }
            var current: String? = modal.ref, seen = Set<String>()
            while let ref = current {
                if path.contains(ref) { return false }
                guard seen.insert(ref).inserted, let owner = inputWindows.first(where: { $0.ref == ref }),
                    !unknownParents.contains(ref) else { return true }
                current = owner.parent
            }
            // Only a complete chain to a positively known independent root
            // proves another document; a missing/unknown/cyclic link cannot.
            return false
        }
        if unresolvedModal {
            throw DriverError("a live modal surface has unresolved ownership", code: .inputUnavailable)
        }
        let blocked = try NativeWindowBoundary.blocked(
            root: root, target: selected.window, windows: inputWindows,
            focused: state.applications.focusedWindow(selected.owner, windows: windows))
        return (selected, blocked)
    }

    /// Native-only rows can supply live containment without supplying AX
    /// content or a modal claim. Reuse the collector's conflict/cycle proof and
    /// require both ends to be unique in the current process incarnation.
    private func nativeInputAttachments(
        _ boundary: NativeApplicationReader.InputBoundary, native: [NativeWindows.Window]
    ) -> [AppSelection.Window] {
        guard let evidence = boundary.parentEvidence else { return [] }
        let axRefs = Set(boundary.windows.map(\.ref))
        guard evidence.parents.keys.contains(where: { !axRefs.contains($0) }) else { return [] }
        let refs = native.map { (ref: state.applications.windowReference($0), window: $0) }
        let byRef = Dictionary(grouping: refs, by: \.ref)
        let byID = Dictionary(grouping: native, by: \.mapping.id)
        return refs.compactMap { ref, window in
            guard !axRefs.contains(ref), byRef[ref]?.count == 1, byID[window.mapping.id]?.count == 1,
                !evidence.unresolved.contains(ref), let parent = evidence.parents[ref],
                !evidence.unresolved.contains(parent), let candidates = byRef[parent], candidates.count == 1,
                let owner = candidates.first?.window, byID[owner.mapping.id]?.count == 1,
                window.parentID == owner.mapping.id, owner.process == window.process
            else { return nil }
            return .init(ref: ref, parent: parent, modal: false)
        }
    }

    /// An unavailable modal proxy is irrelevant only with positive current
    /// native attachment into a different, fully known independent AX root.
    /// Reuse this collector's parent evidence; no historical edge authorizes it.
    private func unrelatedModalHints(
        _ boundary: NativeApplicationReader.InputBoundary, hints: [NativeApplicationReader.ModalHint],
        native: [NativeWindows.Window], root: String
    ) -> Set<String> {
        guard let evidence = boundary.parentEvidence else { return [] }
        let windows = boundary.windows
        func independentRoot(_ ref: String) -> String? {
            var current = ref, seen = Set<String>()
            while seen.insert(current).inserted {
                guard let row = windows.first(where: { $0.ref == current }), !row.parentUnknown,
                    !evidence.unresolved.contains(current), row.parent == evidence.parents[current] else { return nil }
                guard let parent = row.parent else { return row.kind == kAXWindowRole ? row.ref : nil }
                current = parent
            }
            return nil
        }
        guard let selectedRoot = independentRoot(root) else { return [] }
        func nativeOwnerRoot(_ hint: NativeApplicationReader.ModalHint) -> String? {
            guard hint.nativePresence == .live, let expected = hint.identity.native else { return nil }
            var id = expected.id, seen = Set<CGWindowID>()
            for _ in 0..<32 {
                guard seen.insert(id).inserted else { return nil }
                let candidates = native.filter { $0.mapping.id == id }
                guard candidates.count == 1, let current = candidates.first, current.process == expected.process,
                    let parentID = current.parentID, parentID != 0 else { return nil }
                let owners = native.filter { $0.mapping.id == parentID }
                guard owners.count == 1, let parent = owners.first, parent.process == expected.process else { return nil }
                let ref = state.applications.windowReference(current), owner = state.applications.windowReference(parent)
                guard !evidence.unresolved.contains(ref), !evidence.unresolved.contains(owner),
                    evidence.parents[ref] == owner else { return nil }
                if windows.contains(where: { $0.ref == owner }) { return independentRoot(owner) }
                id = parentID
            }
            return nil
        }
        return Set(hints.compactMap { hint in
            guard boundary.unresolvedModalHints.contains(hint.identity.window),
                let owner = nativeOwnerRoot(hint), owner != selectedRoot else { return nil }
            return hint.identity.window
        })
    }

    /// Optional mechanism queries need the same positive lifetime evidence as
    /// retained modal queries. Missing AX proxies cannot establish an ended
    /// physical window, and a failed WindowServer query remains unknown.
    func nativeWindowPresence(_ surface: Surface) -> NativeApplicationReader.ModalPresence? {
        guard let native = surface.native else { return nil }
        return nativePresence(.init(id: native.mapping.id, process: native.process), current: []).presence
    }

    private func nativePresence(
        _ expected: NativeApplicationReader.ModalIdentity.Native, current: [NativeWindows.Window]
    ) -> (presence: NativeApplicationReader.ModalPresence, onScreen: Bool?) {
        if let window = current.first(where: { $0.mapping.id == expected.id }) {
            return (window.process == expected.process ? .live : .ended, window.onScreen)
        }
        let process = current.first(where: { $0.process.pid == expected.process.pid })?.process
            ?? windowServices.process(expected.process.pid)
        guard let process else { return (windowServices.isGone(expected.process.pid) ? .ended : .unknown, nil) }
        guard process == expected.process else { return (.ended, nil) }
        switch windowServices.presence(expected.id) {
        case .present(let row):
            guard (row[kCGWindowNumber as String] as? NSNumber)?.uint32Value == expected.id,
                let pid = (row[kCGWindowOwnerPID as String] as? NSNumber)?.int32Value else { return (.unknown, nil) }
            return (pid == expected.process.pid ? .live : .ended,
                (row[kCGWindowIsOnscreen as String] as? NSNumber)?.boolValue)
        case .absent: return (.ended, nil)
        case .unavailable: return (.unknown, nil)
        }
    }

    func retainedModalHints(owner: NSRunningApplication, native: [NativeWindows.Window]) throws
        -> [NativeApplicationReader.ModalHint]
    {
        var hints: [NativeApplicationReader.ModalHint] = try surfaces.values.filter { $0.owner.isEqual(owner) }.compactMap { surface in
            guard case .retainedModal(let identity)? = surface.queryHint else { return nil }
            try RequestDiagnostics.checkCancellation()
            let state = identity.native.map { nativePresence($0, current: native) }
            return .init(identity: identity, nativePresence: state?.presence, nativeOnScreen: state?.onScreen)
        }
        // A retained CG window can outlive its modal presentation. Query raw
        // visibility once only when the exact live identity lacks that fact;
        // absence from the filtered native directory cannot establish it.
        if hints.contains(where: { $0.identity.isSheet && $0.nativePresence == .live && $0.nativeOnScreen == nil }) {
            try RequestDiagnostics.checkCancellation()
            if let onScreen = windowServices.onScreenWindows() {
                for index in hints.indices where hints[index].nativePresence == .live && hints[index].nativeOnScreen == nil {
                    if let id = hints[index].identity.native?.id { hints[index].nativeOnScreen = onScreen.contains(id) }
                }
            }
        }
        let hidden = hints.filter { $0.identity.isSheet && $0.nativePresence == .live && $0.nativeOnScreen == false }
        try RequestDiagnostics.checkCancellation()
        let parents = hidden.isEmpty ? nil : windowServices.parents(hidden.compactMap { $0.identity.native?.id })
        try RequestDiagnostics.checkCancellation()
        for index in hints.indices {
            if let id = hints[index].identity.native?.id { hints[index].nativeParentID = parents?[id] }
            let hint = hints[index]
            NativePointerTiming.record("modalHintNativeEvidence", metadata: {
                ["window": hint.identity.window, "nativeWindow": hint.identity.native?.id as Any? ?? NSNull(),
                 "presence": hint.nativePresence.map { String(describing: $0) } ?? "AX-only",
                 "onScreen": hint.nativeOnScreen as Any? ?? NSNull(), "parentID": hint.nativeParentID as Any? ?? NSNull(),
                 "observedSheet": hint.identity.isSheet]
            })
        }
        return hints
    }

    func remember(_ observed: Surface) {
        var surface = observed
        if case .observedModal(let isSheet)? = surface.queryHint {
            // A positive ordinary-modal observation replaces an older sheet
            // qualification, even if the native object itself is unchanged.
            surface.queryHint = .retainedModal(.init(
                window: surface.window, element: surface.element,
                native: surface.native.map { .init(id: $0.mapping.id, process: $0.process) }, isSheet: isSheet))
        } else if surface.queryHint == nil, let previous = surfaces[surface.scope],
            previous.owner.isEqual(surface.owner), previous.window == surface.window
        {
            // Missing/new AX details do not erase the prior positive identity.
            // It only asks a future input query; no modality verdict is reused.
            surface.queryHint = previous.queryHint
        }
        surfaces[surface.scope] = surface
    }

    /// Reuse the exact WindowServer/process lifetime proof used for modal
    /// hints. A filtered catalogue, hidden window or unknown AX provider cannot
    /// retire an identity. AX-only unknown surfaces remain until session end.
    private func releaseEndedScopes(owner: NSRunningApplication, native: [NativeWindows.Window]) {
        let ended = surfaces.values.filter { surface in
            if surface.owner.isTerminated && windowServices.isGone(surface.owner.processIdentifier) { return true }
            guard surface.owner.isEqual(owner), let window = surface.native else { return false }
            return nativePresence(.init(id: window.mapping.id, process: window.process), current: native).presence == .ended
        }
        for surface in ended {
            let instance = state.appSelections.get(owner: surface.owner, root: surface.window)
            endedWindows[surface.scope] = (surface.owner, [
                "window": surface.window, "instance": instance.id, "surface": surface.scope
            ])
            if let native = surface.native { state.applications.retireWindowReference(native) }
            state.applications.retireScope(owner: surface.owner, scope: surface.scope)
            surfaces.removeValue(forKey: surface.scope)
        }
        for (scope, root) in applicationRoots
            where root.owner.isTerminated && windowServices.isGone(root.owner.processIdentifier) {
            state.applications.retireScope(owner: root.owner, scope: scope)
            applicationRoots.removeValue(forKey: scope)
        }
    }
    private func result(_ kind: String, began: Int64, reasons: [String]) -> [String: Any] {
        [
            "kind": kind, "session": session, "platform": "macos", "stopReasons": reasons,
            "observed": [
                "id": UUID().uuidString, "startedAt": began, "completedAt": Int64(Date().timeIntervalSince1970 * 1000)
            ]
        ]
    }

    func windowCatalogue(
        appName: String, collect: () throws -> (windows: [[String: Any]], stopReasons: [String])
    ) rethrows -> [String: Any] {
        let began = Int64(Date().timeIntervalSince1970 * 1000)
        let catalogue = try collect()
        var reply = result("windowCatalogue", began: began, reasons: catalogue.stopReasons)
        reply["appName"] = appName
        reply["windows"] = catalogue.windows
        return reply
    }

    func applications(scope: NativeInspectQuery.ApplicationScope = .all(includeEnded: false)) throws -> [String: Any] {
        let began = Int64(Date().timeIntervalSince1970 * 1000)
        let deadline = min((RequestDiagnostics.deadline ?? .infinity) - 0.2, ProcessInfo.processInfo.systemUptime + 3)
        let path: URL?, installation: NativeInstallation?, echo: [String: String]
        switch scope {
        case .all: (path, installation, echo) = (nil, nil, [:])
        case .path(let requested):
            (path, installation, echo) = (URL(fileURLWithPath: requested), nil, ["path": requested])
        case .installation(let id):
            let original = try self.installation(["session": session, "application": id]).identity
            (path, installation, echo) = (original.url, original, ["installation": id])
        }
        var limited = false
        let entries = state.discovery.nativeApplications(path: path) {
            let allowed = !Task.isCancelled && ProcessInfo.processInfo.systemUptime < deadline
            if !allowed { limited = true }
            return allowed
        }
        try RequestDiagnostics.checkCancellation()
        try installation?.validate()
        var reasons = [String]()
        if limited { reasons.append("application catalogue time budget; installed entries may be missing") }
        if entries.count > 4096 { reasons.append("application catalogue entry limit") }
        var value = result("applications", began: began, reasons: reasons).merging(echo) { current, _ in current }
        if case .all(includeEnded: true) = scope {
            value["endedApplications"] = state.discovery.endedApplicationIDs
        }
        // A retained installation scope is observation-only after a launch
        // may have been sent. Only its current running instances can resolve it.
        let eligible = installation == nil ? entries : entries.filter { $0["isRunning"] as? Bool == true }
        value["applications"] = try eligible.prefix(4096).map { item in
            try RequestDiagnostics.checkCancellation()
            return item["isRunning"] as? Bool == false ? retainInstallation(item) : item
        }
        return value
    }

    func collectSurfaces(_ application: String, profile: NativeInspectQuery.SurfaceProfile = .accessibility) throws -> [String: Any] {
        guard ApplicationInstances<NSRunningApplication>.isInstanceSelector(application) else {
            throw DriverError(
                "surface discovery requires an observed running-application id; it never launches an application",
                code: .invalidRequest)
        }
        let app: NSRunningApplication
        if let locate = windowServices.locate { app = try locate(application) }
        else {
            guard case .running(let observed) = try state.discovery.locate(application) else {
                throw DriverError("surface discovery requires an observed running-application id", code: .invalidRequest)
            }
            app = observed
        }
        let began = Int64(Date().timeIntervalSince1970 * 1000)
        var reasons = [String]()
        let appChange = try changeStatus(app, application: application, arm: profile == .accessibility).stamp
        var rootDiscoveryComplete = true
        var provider: [String: Any] = [
            "status": "permissionUnavailable", "attribute": NSNull(), "writeStatus": NSNull()
        ]
        if profile == .visual {
            // This narrow contract avoids provider initialization and content
            // reads. Window-level AX facts remain optional live evidence.
            provider["status"] = "notRequested"
        } else if windowServices.trusted() {
            // Provider initialization is a platform operation, not window
            // activation. Reuse the lifetime-bounded opt-in used by normal
            // reads, and expose acknowledgement without claiming readiness.
            let activation = windowServices.activate?(app) ?? state.accessibilityActivation.ensure(app)
            provider = [
                "status": activation.kind.rawValue, "attribute": activation.attribute?.rawValue as Any? ?? NSNull(),
                "writeStatus": activation.writeStatus?.rawValue as Any? ?? NSNull()
            ]
        }
        // NativeWindows already classifies visible menus. Reuse those exact
        // facts; a second AX menu scan adds latency and can mix presentations.
        let native = windowServices.listIfAvailable(app)
        // Exact presence checks preserve identities even when this projection
        // omitted their windows. No tree traversal is added to this cleanup.
        releaseEndedScopes(owner: app, native: native ?? [])
        if native == nil {
            reasons.append("WindowServer catalogue unavailable")
            rootDiscoveryComplete = false
        }
        if !windowServices.trusted() {
            reasons.append("accessibility permission unavailable; native windows are retained")
            rootDiscoveryComplete = false
        }
        // Reuse the platform's explicit AX/WindowServer correspondence. This
        // describes native ownership, not AppSelection's default-view policy.
        let ax: [NativeApplicationAccess.Window]
        do {
            ax = try state.applications.windows(
                app, nativeWindows: native ?? [], profile: profile,
                modalHints: profile == .visual ? retainedModalHints(owner: app, native: native ?? []) : [],
                reportIssue: {
                    reasons.append($0.detail)
                    if $0.scope == .roots { rootDiscoveryComplete = false }
                })
        } catch {
            try RequestDiagnostics.checkCancellation()
            reasons.append(
                "AX surface catalogue failed; native windows remain available: \(error.localizedDescription)")
            rootDiscoveryComplete = false
            ax = []
        }
        let visual = Dictionary(grouping: native ?? [], by: { state.applications.windowReference($0) })
        let relations = state.applications.windowParents(ax, native: native ?? [], pid: app.processIdentifier)
        if !relations.unresolved.isEmpty {
            let issue = "some native window parent relationships could not be confirmed"
            if !reasons.contains(issue) { reasons.append(issue) }
            rootDiscoveryComplete = false
        }
        var entries = [[String: Any]](), known = Set<String>(), entryLimited = false
        func append(
            window: String, title: String, kind: String, parent: String?, parentUnknown: Bool = false,
            applicationParent: Bool = false, modal: Bool?,
            element: AXUIElement?, contentKind: String = "unknown", contentHost: String? = nil,
            contentEvidence: String = "unclassified", contentHostNode: String? = nil, nativeID: CGWindowID? = nil
        ) throws {
            guard known.insert(window).inserted else { return }
            guard entries.count < 512 else { entryLimited = true; return }
            try RequestDiagnostics.checkCancellation()
            let parent = profile == .visual && parentUnknown ? nil : parent
            let scope = state.applications.observationScope(app, window: element, reference: window)
            let instance = state.appSelections.get(owner: app, root: window)
            let node: String?
            do {
                node = try (profile == .visual ? nil : element).map {
                    try windowServices.registerRoot?($0, app, scope)
                        ?? state.applications.registerNativeRoot($0, app: app, scope: scope)
                }
            } catch let error as DriverError where error.code == .staleTarget {
                try RequestDiagnostics.rethrowCancellation(error)
                try windowServices.owner(app)
                reasons.append("AX root unavailable for window \(window), surface \(scope): \(error.code.rawValue): \(error.description)")
                let matching = visual[window] ?? []
                if matching.count == 1,
                    nativePresence(.init(id: matching[0].mapping.id, process: matching[0].process), current: []).presence == .live
                {
                    // A stale AX proxy does not end the physical window. Let
                    // the existing native loop publish its exact visual row,
                    // without claiming the invalid root or its AX properties.
                    known.remove(window)
                } else {
                    // There is no current native proof for this failed root.
                    // Keep other windows; neither invent an AX capability nor
                    // retire the old lifetime from this local read failure.
                    rootDiscoveryComplete = false
                }
                return
            }
            // AXApplication may contain a native attached window. Confirmed
            // native edges supplement AX; conflicts remain explicit unknowns.
            var parentState = parent == nil ? "unknown" : "known"
            if profile == .visual, !parentUnknown, parent == nil, applicationParent { parentState = "none" }
            if profile != .visual, !parentUnknown, parent == nil, let element, let owner = AXAccess.element(element, kAXParentAttribute),
                AXAccess.read(owner, kAXRoleAttribute).value as? String == kAXApplicationRole
            {
                parentState = "none"
            }
            let matching = visual[window] ?? (nativeID.map { id in (native ?? []).filter { $0.mapping.id == id } } ?? [])
            // A native-only image refresh must not discard an older exact AX
            // binding or its modal-query hint. Retention proves no current AX
            // fact; the same input boundary will revalidate it before dispatch.
            let previous = surfaces[scope]
            // This is the retained physical identity, not an onscreen claim.
            // A current AX root may precede the native projection on reopen;
            // keep its already-proven CG lifetime so later positive closure
            // is still detectable. Capture/input revalidate that exact object.
            let retainedNative = matching.count == 1 ? matching[0]
                : previous?.owner.isEqual(app) == true && previous?.window == window ? previous?.native : nil
            let retainedElement: AXUIElement?
            if profile == .visual, element == nil, let previous, previous.owner.isEqual(app),
                previous.window == window, let oldNative = previous.native,
                matching.count == 1, matching[0].mapping.id == oldNative.mapping.id,
                matching[0].process == oldNative.process
            { retainedElement = previous.element }
            else { retainedElement = element }
            let hint: Surface.QueryHint? = modal == true || (kind == "sheet" && modal != false)
                ? .observedModal(isSheet: kind == "sheet") : nil
            if hint != nil {
                NativePointerTiming.record("observedModalRelation", metadata: {
                    ["window": window, "nativeWindow": matching.first?.mapping.id as Any? ?? NSNull(),
                     "surfaceKind": kind, "parent": parent as Any? ?? NSNull(), "parentUnknown": parentUnknown]
                })
            }
            remember(
                Surface(
                    owner: app, window: window, scope: scope, parent: parent, element: retainedElement,
                    native: retainedNative,
                    queryHint: hint, contentHost: contentHost ?? (profile == .visual ? previous?.contentHost : nil)))
            entries.append([
                "window": window, "instance": instance.id, "surface": scope, "node": node as Any? ?? NSNull(),
                "title": title, "kind": kind, "parent": parent as Any? ?? NSNull(), "parentState": parentState,
                "modal": modal as Any? ?? NSNull(), "nativeWindow": matching.count == 1, "accessibility": node != nil,
                "content": ["kind": contentKind, "host": contentHost as Any? ?? NSNull(),
                            "evidence": contentEvidence, "hostNode": contentHostNode as Any? ?? NSNull()]
            ])
        }
        for item in ax {
            let hostNode: String?
            if let element = item.contentElement, let host = ax.first(where: { $0.ref == item.contentHost }) {
                let scope = state.applications.observationScope(app, window: host.element, reference: host.ref)
                hostNode = state.applications.observedNativeNode(element, app: app, scope: scope)
            } else { hostNode = nil }
            try append(
                window: item.ref, title: item.title, kind: item.kind, parent: item.parent,
                parentUnknown: item.parentUnknown, applicationParent: item.applicationParent,
                modal: item.modal, element: item.element,
                contentKind: item.contentKind, contentHost: item.contentHost,
                contentEvidence: item.contentEvidence, contentHostNode: hostNode, nativeID: item.nativeID)
        }
        for item in native ?? [] {
            let ref = state.applications.windowReference(item)
            let host = item.menu == nil ? nil : (item.menuParent ?? item.menuHost).flatMap { id in
                native?.first { $0.mapping.id == id }.map { state.applications.windowReference($0) }
            } ?? relations.parents[ref]
            let hostNode: String?
            if let menu = item.menu, let host {
                // The physical popup can expose the exact AXMenu already
                // acquired inside its document. Reuse that retained identity;
                // do not rescan the host or equate two same-shaped menus.
                let scope = state.applications.observationScope(app, window: nil, reference: host)
                if let observed = windowServices.observedNode { hostNode = observed(menu, app, scope) }
                else { hostNode = state.applications.observedNativeNode(menu, app: app, scope: scope) }
            } else { hostNode = nil }
            try append(
                window: ref, title: item.mapping.title ?? "", kind: item.menu == nil ? "window" : "menu",
                parent: relations.parents[ref], modal: nil, element: item.menu,
                contentKind: item.menu == nil ? "unknown" : "menu",
                contentHost: host, contentEvidence: item.menu == nil ? "unclassified" : "visibleNativeMenu",
                contentHostNode: hostNode)
        }
        // A read-time application report, independent of working-window
        // selection and input readiness. Never infer it from z-order, main
        // window, title or the foreground application. Unmapped is unknown.
        var focusedWindow: String?
        if windowServices.trusted() {
            let focused: AXUIElement?
            if let read = windowServices.focusedWindow { focused = read(app) }
            else {
                let root = AXUIElementCreateApplication(app.processIdentifier)
                AXMessaging.configure(root)
                focused = AXAccess.element(root, kAXFocusedWindowAttribute)
            }
            if let focused {
                let matches = entries.compactMap { item -> String? in
                    guard let scope = item["surface"] as? String, let element = surfaces[scope]?.element,
                        CFEqual(element, focused)
                    else { return nil }
                    return item["window"] as? String
                }
                if matches.count == 1 { focusedWindow = matches[0] }
            }
        }
        if entryLimited {
            reasons.append("surface catalogue entry limit")
            rootDiscoveryComplete = false
        }
        if entries.contains(where: { $0["parentState"] as? String == "unknown" }) {
            rootDiscoveryComplete = false
        }
        try windowServices.owner(app)
        var value = result("surfaces", began: began, reasons: reasons)
        value["application"] = application
        value["surfaces"] = entries
        // These are positive lifetime facts, not inferred from a visible-only
        // projection's missing rows. Keep compact tombstones for later readers
        // which did not consume the collection that first detected the end.
        value["endedWindows"] = endedWindows.keys.sorted().compactMap { key -> [String: Any]? in
            guard let entry = endedWindows[key], entry.owner.isEqual(app) else { return nil }
            return entry.value
        }.suffix(4096).map { $0 }
        value["rootDiscoveryComplete"] = rootDiscoveryComplete
        value["provider"] = provider
        value["focusedWindow"] = focusedWindow as Any? ?? NSNull()
        if let appChange { value["appChange"] = appChange.wire }
        return value
    }
}
