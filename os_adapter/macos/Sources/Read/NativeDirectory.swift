// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices

/// Discovery and retained scope identities. Selection belongs to Python; this
/// collector never picks a document, launches, activates, or reads its contents.
@MainActor final class NativeDirectory {
    struct Surface {
        enum QueryHint {
            case observedModal
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
    }
    private let state: NativeDesktopState
    private let session: String
    /// Native window adapters share this directory's retained identities. The
    /// defaults are live OS queries; tests replace only these leaf adapters.
    @MainActor struct WindowServices {
        var list: (NSRunningApplication) -> [NativeWindows.Window] = { NativeWindows.list($0) }
        var owner: (NSRunningApplication) throws -> Void = { try BackgroundTarget($0).validate() }
        var process: (pid_t) -> NativeProcessIdentity? = { NativeProcessIdentity.read($0) }
        var info: (CGWindowID) -> [String: Any]? = { WindowServerInfo.window($0) }
        var presence: (CGWindowID) -> WindowServerInfo.Presence = { WindowServerInfo.read($0) }
        var isGone: (pid_t) -> Bool = { NativeProcessIdentity.isGone($0) }
        var role: (AXUIElement) -> String? = { AXAccess.read($0, kAXRoleAttribute).value as? String }
    }
    private let windowServices: WindowServices
    private var surfaces = [String: Surface]()
    private var order = [String]()
    struct ApplicationRoot {
        let owner: NSRunningApplication
        let element: AXUIElement
        let scope: String
    }
    private var applicationRoots = [String: ApplicationRoot]()
    private var rootOrder = [String]()
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
    func windowElement(_ window: String, owner: NSRunningApplication) -> AXUIElement? {
        surfaces.values.first { $0.window == window && $0.owner.isEqual(owner) }?.element
    }
    func applicationRoot(_ id: String) -> ApplicationRoot? { applicationRoots[id] }

    /// Application UI such as the menu bar is not a document window. When a
    /// caller has selected a document, keep that binding and its lifetime; a
    /// read of a global root cannot revive a closed working window.
    func applicationRootScope(instance: String, root: String?, surface: String) throws -> ApplicationRoot? {
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
        guard let current = AXAccess.element(app, kAXMenuBarAttribute), CFEqual(current, selected.element) else {
            throw DriverError("the observed application menu root changed or is unavailable", code: .staleTarget)
        }
        return selected
    }

    func collectRoots(_ application: String) throws -> [String: Any] {
        guard ApplicationInstances<NSRunningApplication>.isInstanceSelector(application),
            case .running(let app) = try state.discovery.locate(application)
        else { throw DriverError("root discovery requires an observed running-application id", code: .invalidRequest) }
        let began = Int64(Date().timeIntervalSince1970 * 1000)
        var roots = [[String: Any]](), reasons = [String]()
        if AXIsProcessTrusted() {
            _ = state.accessibilityActivation.ensure(app)
            let element = AXUIElementCreateApplication(app.processIdentifier)
            AXMessaging.configure(element)
            let read = AXAccess.read(element, kAXMenuBarAttribute)
            switch read {
            case .value(let raw) where CFGetTypeID(raw) == AXUIElementGetTypeID():
                let menu = unsafeDowncast(raw, to: AXUIElement.self)
                let scope = state.applications.observationScope(app, window: nil)
                let node = try state.applications.registerNativeRoot(menu, app: app, scope: scope)
                let instance = state.appSelections.get(owner: app, root: nil)
                applicationRoots[scope] = ApplicationRoot(owner: app, element: menu, scope: scope)
                rootOrder.removeAll { $0 == scope }
                rootOrder.append(scope)
                while rootOrder.count > 1024 { applicationRoots.removeValue(forKey: rootOrder.removeFirst()) }
                roots.append(["kind": "menuBar", "instance": instance.id, "surface": scope, "node": node])
            case .unsupported, .unavailable(.noValue): break
            default: reasons.append("application menu root could not be read")
            }
        } else {
            reasons.append("accessibility permission unavailable")
        }
        try BackgroundTarget(app).validate()
        try RequestDiagnostics.checkCancellation()
        var value = result("roots", began: began, reasons: reasons)
        value["application"] = application
        value["roots"] = roots
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
        var modalHints = [NativeApplicationReader.ModalHint]()
        for surface in surfaces.values where surface.owner.isEqual(selected.owner) {
            guard case .retainedModal(let identity)? = surface.queryHint else { continue }
            try RequestDiagnostics.checkCancellation()
            modalHints.append(.init(identity: identity, nativePresence: identity.native.map { modalPresence($0, current: native) }))
        }
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
            guard let row = windows.first(where: { $0.ref == ref }), !row.parentUnknown else {
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
                guard seen.insert(ref).inserted, let owner = windows.first(where: { $0.ref == ref }),
                    !owner.parentUnknown else { return true }
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
            root: root, target: selected.window,
            windows: windows.map { .init(ref: $0.ref, parent: $0.parent, modal: $0.modal == true) },
            focused: state.applications.focusedWindow(selected.owner, windows: windows))
        return (selected, blocked)
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

    private func modalPresence(
        _ expected: NativeApplicationReader.ModalIdentity.Native, current: [NativeWindows.Window]
    ) -> NativeApplicationReader.ModalPresence {
        if let window = current.first(where: { $0.mapping.id == expected.id }) {
            return window.process == expected.process ? .live : .ended
        }
        let process = current.first(where: { $0.process.pid == expected.process.pid })?.process
            ?? windowServices.process(expected.process.pid)
        guard let process else { return windowServices.isGone(expected.process.pid) ? .ended : .unknown }
        guard process == expected.process else { return .ended }
        switch windowServices.presence(expected.id) {
        case .present(let row):
            guard (row[kCGWindowNumber as String] as? NSNumber)?.uint32Value == expected.id,
                let pid = (row[kCGWindowOwnerPID as String] as? NSNumber)?.int32Value else { return .unknown }
            return pid == expected.process.pid ? .live : .ended
        case .absent: return .ended
        case .unavailable: return .unknown
        }
    }

    func remember(_ observed: Surface) {
        var surface = observed
        if case .observedModal? = surface.queryHint {
            surface.queryHint = .retainedModal(.init(
                window: surface.window, element: surface.element,
                native: surface.native.map { .init(id: $0.mapping.id, process: $0.process) }))
        } else if surface.queryHint == nil, let previous = surfaces[surface.scope],
            previous.owner.isEqual(surface.owner), previous.window == surface.window
        {
            // Missing/new AX details do not erase the prior positive identity.
            // It only asks a future input query; no modality verdict is reused.
            surface.queryHint = previous.queryHint
        }
        surfaces[surface.scope] = surface
        order.removeAll { $0 == surface.scope }
        order.append(surface.scope)
        while order.count > 1024 { surfaces.removeValue(forKey: order.removeFirst()) }
    }
    private func result(_ kind: String, began: Int64, reasons: [String]) -> [String: Any] {
        [
            "kind": kind, "session": session, "platform": "macos", "stopReasons": reasons,
            "observed": [
                "id": UUID().uuidString, "startedAt": began, "completedAt": Int64(Date().timeIntervalSince1970 * 1000)
            ]
        ]
    }

    func applications() throws -> [String: Any] {
        let began = Int64(Date().timeIntervalSince1970 * 1000)
        let deadline = min((RequestDiagnostics.deadline ?? .infinity) - 0.2, ProcessInfo.processInfo.systemUptime + 3)
        var limited = false
        let entries = state.discovery.nativeApplications {
            let allowed = !Task.isCancelled && ProcessInfo.processInfo.systemUptime < deadline
            if !allowed { limited = true }
            return allowed
        }
        try RequestDiagnostics.checkCancellation()
        var reasons = [String]()
        if limited { reasons.append("application catalogue time budget; installed entries may be missing") }
        if entries.count > 4096 { reasons.append("application catalogue entry limit") }
        var value = result("applications", began: began, reasons: reasons)
        value["applications"] = try entries.prefix(4096).map { item in
            try RequestDiagnostics.checkCancellation()
            return item["isRunning"] as? Bool == false ? retainInstallation(item) : item
        }
        return value
    }

    func collectSurfaces(_ application: String) throws -> [String: Any] {
        guard ApplicationInstances<NSRunningApplication>.isInstanceSelector(application),
            case .running(let app) = try state.discovery.locate(application)
        else {
            throw DriverError(
                "surface discovery requires an observed running-application id; it never launches an application",
                code: .invalidRequest)
        }
        let began = Int64(Date().timeIntervalSince1970 * 1000)
        var reasons = [String]()
        var provider: [String: Any] = [
            "status": "permissionUnavailable", "attribute": NSNull(), "writeStatus": NSNull()
        ]
        if AXIsProcessTrusted() {
            // Provider initialization is a platform operation, not window
            // activation. Reuse the lifetime-bounded opt-in used by normal
            // reads, and expose acknowledgement without claiming readiness.
            let activation = state.accessibilityActivation.ensure(app)
            provider = [
                "status": activation.kind.rawValue, "attribute": activation.attribute?.rawValue as Any? ?? NSNull(),
                "writeStatus": activation.writeStatus?.rawValue as Any? ?? NSNull()
            ]
        }
        let native = NativeWindows.listIfAvailable(app)
        if native == nil { reasons.append("WindowServer catalogue unavailable") }
        if !AXIsProcessTrusted() { reasons.append("accessibility permission unavailable; native windows are retained") }
        // Reuse the platform's explicit AX/WindowServer correspondence. This
        // describes native ownership, not AppSelection's default-view policy.
        let ax: [NativeApplicationAccess.Window]
        do {
            ax = try state.applications.windows(app, nativeWindows: native ?? [], reportIssue: { reasons.append($0) })
        } catch {
            try RequestDiagnostics.checkCancellation()
            reasons.append(
                "AX surface catalogue failed; native windows remain available: \(error.localizedDescription)")
            ax = []
        }
        let visual = Dictionary(grouping: native ?? [], by: { state.applications.windowReference($0) })
        let relations = state.applications.windowParents(ax, native: native ?? [], pid: app.processIdentifier)
        if !relations.unresolved.isEmpty {
            let issue = "some native window parent relationships could not be confirmed"
            if !reasons.contains(issue) { reasons.append(issue) }
        }
        var entries = [[String: Any]](), known = Set<String>()
        func append(
            window: String, title: String, kind: String, parent: String?, parentUnknown: Bool = false, modal: Bool?,
            element: AXUIElement?
        ) throws {
            guard known.insert(window).inserted else { return }
            guard entries.count < 512 else { return }
            try RequestDiagnostics.checkCancellation()
            let scope = state.applications.observationScope(app, window: element, reference: window)
            let instance = state.appSelections.get(owner: app, root: window)
            let node = try element.map { try state.applications.registerNativeRoot($0, app: app, scope: scope) }
            // AXApplication may contain a native attached window. Confirmed
            // native edges supplement AX; conflicts remain explicit unknowns.
            var parentState = parent == nil ? "unknown" : "known"
            if !parentUnknown, parent == nil, let element, let owner = AXAccess.element(element, kAXParentAttribute),
                AXAccess.read(owner, kAXRoleAttribute).value as? String == kAXApplicationRole
            {
                parentState = "none"
            }
            let matching = visual[window] ?? []
            remember(
                Surface(
                    owner: app, window: window, scope: scope, parent: parent, element: element,
                    native: matching.count == 1 ? matching[0] : nil,
                    queryHint: modal == true || (kind == "sheet" && modal != false) ? .observedModal : nil))
            entries.append([
                "window": window, "instance": instance.id, "surface": scope, "node": node as Any? ?? NSNull(),
                "title": title, "kind": kind, "parent": parent as Any? ?? NSNull(), "parentState": parentState,
                "modal": modal as Any? ?? NSNull(), "nativeWindow": visual[window] != nil, "accessibility": node != nil
            ])
        }
        for item in ax {
            try append(
                window: item.ref, title: item.title, kind: item.kind, parent: item.parent,
                parentUnknown: item.parentUnknown, modal: item.modal, element: item.element)
        }
        for item in native ?? [] {
            let ref = state.applications.windowReference(item)
            try append(
                window: ref, title: item.mapping.title ?? "", kind: "window", parent: relations.parents[ref],
                modal: nil, element: nil)
        }
        // A read-time application report, independent of working-window
        // selection and input readiness. Never infer it from z-order, main
        // window, title or the foreground application. Unmapped is unknown.
        var focusedWindow: String?
        if AXIsProcessTrusted() {
            let root = AXUIElementCreateApplication(app.processIdentifier)
            AXMessaging.configure(root)
            if let focused = AXAccess.element(root, kAXFocusedWindowAttribute) {
                let matches = entries.compactMap { item -> String? in
                    guard let scope = item["surface"] as? String, let element = surfaces[scope]?.element,
                        CFEqual(element, focused)
                    else { return nil }
                    return item["window"] as? String
                }
                if matches.count == 1 { focusedWindow = matches[0] }
            }
        }
        if known.count > entries.count { reasons.append("surface catalogue entry limit") }
        try BackgroundTarget(app).validate()
        var value = result("surfaces", began: began, reasons: reasons)
        value["application"] = application
        value["surfaces"] = entries
        value["provider"] = provider
        value["focusedWindow"] = focusedWindow as Any? ?? NSNull()
        return value
    }
}
