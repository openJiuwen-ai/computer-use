// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices

@main struct NativeVisualDirectoryTests {
    @MainActor final class Scene {
        let app = NSRunningApplication.current
        let application = AXUIElementCreateApplication(19000)
        let window = AXUIElementCreateApplication(19001)
        let files = AXUIElementCreateApplication(19002)
        let cell = AXUIElementCreateApplication(19003)
        var attributes = [CFHashCode: [String: CFTypeRef]]()
        var children = [CFHashCode: [AXUIElement]]()
        var content = [String]()
        var activation = 0
        var trusted = true
        var declared = [AXUIElement]()
        var declarationUnavailable = false
        var unavailableChildren = Set<CFHashCode>()
        var ids = [CFHashCode: CGWindowID]()
        var focused: AXUIElement?
        var inputWindow: AXUIElement?
        var focusedElement: AXUIElement?
        var nativeAvailable = true
        var hintUnavailable = false
        var hintCalls = 0
        var nativeIDs: [CGWindowID] = [380]
        var nativeMenus = [CGWindowID: AXUIElement]()
        var nativeMenuHosts = [CGWindowID: CGWindowID]()
        var hiddenNativeIDs = Set<CGWindowID>()
        var nativePresenceUnavailable = false
        var failedApplications = Set<String>()
        var locations = [String]()
        var elapsed = 100.0
        var onLocate: (() -> Void)?
        var nextID: pid_t = 19100
        var rootRegistration: ((AXUIElement, String) throws -> String)?
        var observedNode: ((AXUIElement, String) -> String?)?
        var ownerFailure: DriverError?
        lazy var access = NativeApplicationAccess(windowQuery: api())
        lazy var state = NativeDesktopState(applications: access, appSelections: AppSelections(validateOwner: { _ in }))
        lazy var directory: NativeDirectory = {
            var services = NativeDirectory.WindowServices()
            services.locate = { name in
                self.locations.append(name)
                self.onLocate?()
                if self.failedApplications.contains(name) {
                    throw DriverError("scripted provider failure", code: .observationIncomplete)
                }
                return self.app
            }
            services.listIfAvailable = { _ in self.nativeAvailable ? self.windows() : nil }
            services.list = { _ in self.windows() }
            services.trusted = { self.trusted }
            services.activate = { _ in self.activation += 1; return .init(kind: .alreadyEnabled) }
            services.focusedWindow = { _ in self.focused }
            services.registerRoot = { element, _, scope in
                if let register = self.rootRegistration { return try register(element, scope) }
                self.content.append("root body registration poison")
                throw DriverError("scripted content poison", code: .observationIncomplete)
            }
            services.observedNode = { element, _, scope in self.observedNode?(element, scope) }
            services.owner = { _ in if let error = self.ownerFailure { throw error } }
            services.process = { _ in self.process }
            services.info = { id in self.nativeIDs.contains(id) || self.hiddenNativeIDs.contains(id)
                ? [kCGWindowOwnerPID as String: self.app.processIdentifier, kCGWindowNumber as String: id] : nil }
            services.presence = { id in
                if self.nativePresenceUnavailable { return .unavailable }
                guard self.nativeIDs.contains(id) || self.hiddenNativeIDs.contains(id) else { return .absent }
                return .present([kCGWindowOwnerPID as String: self.app.processIdentifier,
                                 kCGWindowNumber as String: id,
                                 kCGWindowIsOnscreen as String: self.nativeIDs.contains(id)])
            }
            services.role = { self.value($0, kAXRoleAttribute) as? String }
            return NativeDirectory(state: state, session: "visual-scene", windowServices: services)
        }()
        var process: NativeProcessIdentity {
            .init(pid: app.processIdentifier, parent: 0, seconds: 1, microseconds: 0, executable: "/test-window-provider")
        }
        init() {
            put(application, kAXRoleAttribute, kAXApplicationRole as CFString)
            for (node, role, parent) in [(window, kAXWindowRole, application), (files, "AXTable", window), (cell, "AXCell", files)] {
                put(node, kAXRoleAttribute, role as CFString)
                put(node, kAXParentAttribute, parent)
            }
            put(window, kAXTitleAttribute, "Native file view" as CFString)
            put(window, kAXSubroleAttribute, kAXStandardWindowSubrole as CFString)
            put(window, kAXModalAttribute, kCFBooleanFalse)
            put(window, "AXSheets", [] as CFArray)
            children[CFHash(window)] = [files]
            children[CFHash(files)] = Array(repeating: cell, count: 512)
            declared = [window]
            ids[CFHash(window)] = 380
            focused = window
            inputWindow = window
            focusedElement = cell
        }
        func node(_ role: String, parent: AXUIElement? = nil, nativeID: CGWindowID? = nil) -> AXUIElement {
            let node = AXUIElementCreateApplication(nextID); nextID += 1
            put(node, kAXRoleAttribute, role as CFString)
            put(node, "AXSheets", [] as CFArray)
            if let parent { put(node, kAXParentAttribute, parent) }
            if let nativeID { ids[CFHash(node)] = nativeID; nativeIDs.append(nativeID) }
            return node
        }
        func put(_ node: AXUIElement, _ key: String, _ value: CFTypeRef) {
            attributes[CFHash(node), default: [:]][key] = value
        }
        func value(_ node: AXUIElement, _ key: String) -> CFTypeRef? {
            if CFEqual(node, cell) { content.append("focused-cell." + key) }
            if CFEqual(node, files), [kAXTitleAttribute, kAXSubroleAttribute, kAXDocumentAttribute].contains(key) {
                content.append("declared control presentation." + key)
            }
            if CFEqual(node, application) {
                if key == kAXWindowsAttribute { return declarationUnavailable ? nil : declared as CFArray }
                if key == kAXFocusedWindowAttribute || key == kAXMainWindowAttribute { return focused }
                if key == kAXFocusedUIElementAttribute { content.append("AXFocusedUIElement"); return focusedElement }
            }
            return attributes[CFHash(node)]?[key]
        }
        func api() -> NativeWindowQueryAPI {
            var api = NativeWindowQueryAPI(attribute: { self.value($0, $1) })
            api.trusted = { self.trusted }
            api.application = { _ in self.application }
            api.read = { node, key in self.value(node, key).map(AXRead.value) ?? .unsupported }
            api.element = { node, key in
                guard let raw = self.value(node, key), CFGetTypeID(raw) == AXUIElementGetTypeID() else { return nil }
                return unsafeDowncast(raw, to: AXUIElement.self)
            }
            api.pid = { _ in (.success, self.app.processIdentifier) }
            api.inputWindow = { _, _, _ in self.content.append("inputWindow focused-control policy"); return self.inputWindow }
            api.presentedWindow = { node, _, _ in node }
            api.nativeID = { self.ids[CFHash($0)] }
            api.hint = { node, _ in
                self.hintCalls += 1
                if self.hintUnavailable { return nil }
                return self.ids[CFHash(node)].map {
                    .init(frame: CGRect(x: 10, y: 10, width: 500, height: 400), title: "", id: $0)
                }
            }
            api.relations.count = { node, _ in
                if self.unavailableChildren.contains(CFHash(node)) { return (.cannotComplete, 0) }
                if CFEqual(node, self.files) { self.content.append("file-list.AXChildren poison"); return (.cannotComplete, 0) }
                return (.success, self.children[CFHash(node)]?.count ?? 0)
            }
            api.relations.range = { node, _, count in (.success, Array((self.children[CFHash(node)] ?? []).prefix(count)) as CFArray) }
            api.relations.single = { _, _ in
                self.content.append("file-list.AXChildren fallback poison")
                return (.cannotComplete, nil)
            }
            return api
        }
        func windows() -> [NativeWindows.Window] {
            nativeIDs.map { id in .init(application: app, process: process,
                   mapping: .init(id: id, pid: app.processIdentifier, frame: CGRect(x: 10, y: 10, width: 500, height: 400),
                                  title: "Native file view", width: 500, height: 400), onScreen: true,
                   level: nativeMenus[id] == nil ? 0 : Int(CGWindowLevelForKey(.popUpMenuWindow)),
                   menu: nativeMenus[id], menuHost: nativeMenuHosts[id], parentID: 0) }
        }
        func collect(_ profile: String? = "visual") throws -> [String: Any] {
            var raw: [String: Any] = ["kind": "surfaces", "application": "instance:visual-provider"]
            if let profile { raw["profile"] = profile }
            guard case .surfaces(let application, let parsed) = try NativeInspectQuery(raw) else { fatalError("wrong query") }
            return try directory.collectSurfaces(application, profile: parsed)
        }
    }
    @MainActor static func main() throws {
        let scene = Scene()
        var result: [String: Any]?, error: String?
        do { result = try scene.collect() } catch let caught { error = String(describing: caught) }
        var report = try JSONSerialization.data(withJSONObject: ["activation": scene.activation, "content": scene.content,
              "queryProfileAccepted": true, "directoryError": error as Any? ?? NSNull()], options: [.sortedKeys])
        report.append(10)
        try FileHandle.standardOutput.write(contentsOf: report)
        precondition(scene.content.isEmpty, "visual profile reached original content poison")
        precondition(scene.activation == 0, "visual profile initialized provider")
        let rows = result?["surfaces"] as? [[String: Any]] ?? []
        precondition(rows.count == 1, "native window disappeared with unavailable control tree")
        let row = rows[0]
        precondition(row["node"] is NSNull, "image discovery must not register body nodes")
        let scope = try scene.directory.windowScope(instance: row["instance"] as! String,
            root: row["window"] as? String, surface: row["surface"] as! String)
        precondition(scope.surface.native?.mapping.id == 380, "original image capture identity unavailable")
        precondition(row["parentState"] as? String == "none", "fresh application parent was lost")
        precondition((result?["provider"] as? [String: Any])?["status"] as? String == "notRequested")
        try exactVisualIdentitySurvivesOptionalHintFailure()
        try visualWindowFacts()
        try controlMenuKeepsLogicalHostWithoutPhysicalParent()
        try controlMenuReusesOnlyExactPreviouslyObservedHostNode()
        try dropdownContentIsAttachedWithoutInventingNativeParent()
        try sharedPhysicalWindowKeepsDistinctContentSources()
        try applicationRootIsNotAWindow()
        try rootCoverageIsIndependentOfDescendantGaps()
        try nativeOnlyRefreshRetainsBinding()
        try hiddenAndUnknownNativeLifetimesCanReappear()
        try exactNativeHintKeepsIdentityAcrossOnscreenProjectionAndAXProxyChanges()
        try duplicateNativeHintsDoNotMergeIndependentAXRoots()
        try unprovenExactHintDoesNotAllocateAnUnownedPhysicalLifetime()
        try endedNativeHintGetsNewIdentityBeforeNativeProjection()
        try endedNativeLifetimeNeverReusesWindowOrScope()
        try staleRootRegistrationKeepsOtherWindowsAndNativeVisualBinding()
        try staleAXOnlyRootIsOmittedWithoutHidingHealthyWindows()
        try rootRegistrationCancellationAndOwnerFailureRemainFatal()
        try declaredControlRootDoesNotOpenItsContents()
        try legacyContentPositiveControl()
        try visualBatchesRetainProfileFailuresAndDeadline()
        try desktopAndApplicationShareNativeIdentity()
        confirmedHelperMembershipDoesNotGuessByPath()
        print("NativeVisualDirectoryTests passed")
    }

    @MainActor static func exactVisualIdentitySurvivesOptionalHintFailure() throws {
        let scene = Scene()
        scene.hintUnavailable = true
        let rows = try scene.collect()["surfaces"] as! [[String: Any]]
        precondition(scene.hintCalls == 0, "an exact visual ID needs no optional geometry enrichment")
        precondition(rows.count == 1 && rows[0]["nativeWindow"] as? Bool == true)
        let retained = scene.directory.surface(rows[0]["surface"] as! String)
        precondition(retained?.element.map { CFEqual($0, scene.window) } == true)
        precondition(retained?.native?.mapping.id == 380)
    }

    @MainActor static func controlMenuKeepsLogicalHostWithoutPhysicalParent() throws {
        let scene = Scene()
        let menu = scene.node(kAXMenuRole)
        scene.nativeIDs.append(381)
        scene.nativeMenus[381] = menu
        scene.nativeMenuHosts[381] = 380
        let result = try scene.collect()
        let rows = result["surfaces"] as? [[String: Any]] ?? []
        let menuRow = rows.first { ($0["content"] as? [String: Any])?["kind"] as? String == "menu" }!
        let hostRow = rows.first { $0["window"] as? String != menuRow["window"] as? String }!
        let content = menuRow["content"] as! [String: Any]
        precondition(content["host"] as? String == hostRow["window"] as? String,
                     "a discovered control menu must retain its proven document host for model delivery")
        precondition(menuRow["parent"] is NSNull, "logical AX menu ownership must not invent physical parentage")
        let scope = try scene.directory.windowScope(instance: menuRow["instance"] as! String,
            root: menuRow["window"] as? String, surface: menuRow["surface"] as! String)
        precondition(scope.surface.native?.menu.map { CFEqual($0, menu) } == true)
        precondition(scope.surface.contentHost == hostRow["window"] as? String)
    }

    @MainActor static func controlMenuReusesOnlyExactPreviouslyObservedHostNode() throws {
        let scene = Scene()
        let menu = scene.node(kAXMenuRole)
        scene.nativeIDs.append(381)
        scene.nativeMenus[381] = menu
        scene.nativeMenuHosts[381] = 380
        let firstRows = try scene.collect()["surfaces"] as! [[String: Any]]
        let host = firstRows.first { ($0["content"] as? [String: Any])?["kind"] as? String != "menu" }!
        let scope = host["surface"] as! String
        func menuContent(_ rows: [[String: Any]]) -> [String: Any] {
            rows.first { ($0["content"] as? [String: Any])?["kind"] as? String == "menu" }!["content"] as! [String: Any]
        }
        precondition(menuContent(firstRows)["hostNode"] is NSNull,
                     "a menu not yet acquired in its host must not invent a shared node")
        var lookups = 0
        scene.observedNode = { element, observedScope in
            lookups += 1
            return CFEqual(element, menu) && observedScope == scope ? "host-menu-node" : nil
        }
        let rows = try scene.collect()["surfaces"] as! [[String: Any]]
        precondition(menuContent(rows)["hostNode"] as? String == "host-menu-node",
                     "native menu omitted its exact previously observed host node, duplicating acquired content")
        precondition(lookups == 1 && scene.content.isEmpty,
                     "menu reuse must consult retained identity once, without opening host contents")

        // Same host, role and physical pixels do not make a new AX menu the
        // old object. Missing exact evidence must leave independent content.
        scene.nativeMenus[381] = scene.node(kAXMenuRole)
        let replacement = try scene.collect()["surfaces"] as! [[String: Any]]
        precondition(menuContent(replacement)["hostNode"] is NSNull,
                     "replacement AX menu inherited another object's observed host node")
        scene.nativeMenuHosts.removeValue(forKey: 381)
        let unattached = try scene.collect()["surfaces"] as! [[String: Any]]
        precondition(menuContent(unattached)["hostNode"] is NSNull,
                     "a native menu without a proven host borrowed an unrelated node")
    }

    @MainActor static func confirmedHelperMembershipDoesNotGuessByPath() {
        let ownership = NativeProcessOwnership()
        let owner = NativeProcessIdentity(pid: 101, parent: 1, seconds: 10, microseconds: 0,
            executable: "/Example.app/Contents/MacOS/main")
        let helper = NativeProcessIdentity(pid: 102, parent: owner.pid, seconds: 20, microseconds: 0,
            executable: "/Example.app/Contents/Frameworks/helper")
        let unrelated = NativeProcessIdentity(pid: 103, parent: 1, seconds: 20, microseconds: 0,
            executable: helper.executable)
        let outside = NativeProcessIdentity(pid: 104, parent: owner.pid, seconds: 20, microseconds: 0,
            executable: "/Another.app/Contents/MacOS/helper")
        var processes = [owner.pid: owner, helper.pid: helper, unrelated.pid: unrelated, outside.pid: outside]
        let bundle = URL(fileURLWithPath: "/Example.app")
        precondition(ownership.belongs(helper, to: owner, bundle: bundle, read: { processes[$0] }))
        precondition(!ownership.belongs(unrelated, to: owner, bundle: bundle, read: { processes[$0] }),
            "same executable path alone established helper ownership")
        precondition(!ownership.belongs(outside, to: owner, bundle: bundle, read: { processes[$0] }),
            "ancestry alone merged an unrelated application")
        processes[owner.pid] = .init(pid: owner.pid, parent: 1, seconds: 30, microseconds: 0,
            executable: owner.executable)
        precondition(!ownership.belongs(helper, to: owner, bundle: bundle, read: { processes[$0] }),
            "a replacement owner inherited an earlier process membership claim")
    }

    @MainActor static func dropdownContentIsAttachedWithoutInventingNativeParent() throws {
        for physicalPopup in [false, true] {
            let scene = Scene()
            let opener = scene.node("AXComboBox", parent: scene.window)
            let popup = scene.node(kAXWindowRole, parent: scene.application,
                                   nativeID: physicalPopup ? 381 : nil)
            let list = scene.node("AXList", parent: popup)
            scene.put(popup, kAXSubroleAttribute, kAXDialogSubrole as CFString)
            scene.put(popup, kAXModalAttribute, kCFBooleanFalse)
            scene.children[CFHash(scene.window)] = [opener]
            scene.children[CFHash(opener)] = [list]
            scene.children[CFHash(popup)] = [list]
            scene.children[CFHash(list)] = []
            scene.focusedElement = opener
            scene.declared = [scene.window, popup]
            scene.rootRegistration = { element, _ in "node:\(CFHash(element))" }
            let rows = try scene.collect(nil)["surfaces"] as! [[String: Any]]
            let form = rows.first { $0["node"] as? String == "node:\(CFHash(scene.window))" }!
            let options = rows.first { $0["node"] as? String == "node:\(CFHash(popup))" }!
            let content = options["content"] as? [String: Any]
            precondition(content?["kind"] as? String == "menu", "AX dropdown became an independent work window")
            precondition(content?["host"] as? String == form["window"] as? String, "exact forward host was lost")
            precondition(options["parent"] is NSNull, "logical host was forged into native window parent")
            precondition(options["node"] is String, "AX-only content must stay addressable")

            // A distinct list, even with the same role/title and position,
            // cannot inherit the first list's host or input boundary.
            let unrelated = scene.node("AXList", parent: popup)
            scene.children[CFHash(popup)] = [unrelated]
            let changed = try scene.collect(nil)["surfaces"] as! [[String: Any]]
            let separate = changed.first { $0["node"] as? String == "node:\(CFHash(popup))" }!
            precondition((separate["content"] as? [String: Any])?["host"] is NSNull,
                         "same-shaped AX content inherited another object's attachment")
        }
    }

    @MainActor static func sharedPhysicalWindowKeepsDistinctContentSources() throws {
        let scene = Scene()
        let source = scene.node(kAXWindowRole, parent: scene.application)
        scene.ids[CFHash(source)] = 380
        scene.declared.append(source)
        scene.rootRegistration = { element, _ in "node:\(CFHash(element))" }
        let rows = try scene.collect(nil)["surfaces"] as! [[String: Any]]
        let form = rows.first { $0["node"] as? String == "node:\(CFHash(scene.window))" }!
        let extra = rows.first { $0["node"] as? String == "node:\(CFHash(source))" }!
        precondition(form["window"] as? String != extra["window"] as? String,
                     "sharing pixels must not merge distinct AX object identities")
        precondition((extra["content"] as? [String: Any])?["host"] as? String == form["window"] as? String,
                     "two AX sources for one physical window became independent work windows")
        precondition(rows.count == 2 && extra["nativeWindow"] as? Bool == true,
                     "shared sources must retain their own AX input and accurate physical capture")
        scene.put(source, kAXModalAttribute, kCFBooleanTrue)
        let updated = try scene.collect(nil)["surfaces"] as! [[String: Any]]
        let modalSource = updated.first { $0["node"] as? String == "node:\(CFHash(source))" }!
        precondition((modalSource["content"] as? [String: Any])?["evidence"] as? String == "nativeWindowID",
                     "a modal fact erased independent evidence that these sources share physical pixels")
    }

    @MainActor static func applicationRootIsNotAWindow() throws {
        let scene = Scene()
        scene.declared = [scene.application]
        scene.focused = scene.application
        scene.inputWindow = scene.application
        scene.focusedElement = nil
        scene.rootRegistration = { element, _ in "node:\(CFHash(element))" }
        let rows = try scene.collect(nil)["surfaces"] as! [[String: Any]]
        let source = rows.first { ($0["content"] as? [String: Any])?["kind"] as? String == "application" }
        precondition(source?["node"] is String && rows.contains { $0["node"] is NSNull && $0["nativeWindow"] as? Bool == true },
                     "AXApplication must remain a source without replacing the independent native window")
    }

    @MainActor static func desktopAndApplicationShareNativeIdentity() throws {
        let scene = Scene()
        scene.trusted = false
        let native = scene.windows()[0]
        let desktop = scene.directory.retainDesktopWindow(native, owner: scene.app)
        precondition(scene.content.isEmpty && scene.activation == 0,
            "desktop identity registration read AX or initialized a provider")
        precondition(desktop["parentState"] as? String == "unknown" && desktop["node"] is NSNull)
        let application = try scene.collect()["surfaces"] as! [[String: Any]]
        for key in ["window", "surface", "instance"] {
            precondition(application[0][key] as? String == desktop[key] as? String,
                "desktop and application allocated different native identities")
        }
        let moved = NativeWindows.Window(application: scene.app, process: native.process,
            mapping: .init(id: native.mapping.id, pid: native.process.pid,
                frame: CGRect(x: 200, y: 300, width: 700, height: 500), title: "Renamed", width: 700, height: 500),
            onScreen: true)
        let again = scene.directory.retainDesktopWindow(moved, owner: scene.app)
        for key in ["window", "surface", "instance"] {
            precondition(again[key] as? String == desktop[key] as? String, "title/movement changed identity")
        }
        let selected = try scene.directory.windowScope(instance: desktop["instance"] as! String,
            root: desktop["window"] as? String, surface: desktop["surface"] as! String)
        precondition(selected.surface.native?.mapping.frame == moved.mapping.frame,
            "native mapping did not refresh independently from identity")
        scene.nativeIDs = [381]
        let replacement = scene.directory.retainDesktopWindow(scene.windows()[0], owner: scene.app)
        precondition(replacement["window"] as? String != desktop["window"] as? String)
        do {
            _ = try scene.directory.windowScope(instance: desktop["instance"] as! String,
                root: desktop["window"] as? String, surface: desktop["surface"] as! String)
            preconditionFailure("closed desktop window retargeted to another application window")
        } catch let error as DriverError { precondition(error.code == .staleTarget) }
        _ = try scene.directory.windowScope(instance: replacement["instance"] as! String,
            root: replacement["window"] as? String, surface: replacement["surface"] as! String)
        let newProcess = NativeProcessIdentity(pid: native.process.pid, parent: native.process.parent,
            seconds: native.process.seconds + 1, microseconds: native.process.microseconds,
            executable: native.process.executable)
        let reincarnated = NativeWindows.Window(application: scene.app, process: newProcess,
            mapping: native.mapping, onScreen: true)
        let changed = scene.directory.retainDesktopWindow(reincarnated, owner: scene.app)
        precondition(changed["window"] as? String != desktop["window"] as? String,
            "a replacement process revived the prior native reference")
    }

    @MainActor static func rootCoverageIsIndependentOfDescendantGaps() throws {
        let scene = Scene()
        let complete = try scene.collect()
        precondition(complete["rootDiscoveryComplete"] as? Bool == true)
        scene.unavailableChildren.insert(CFHash(scene.window))
        let partial = try scene.collect()
        precondition(!(partial["stopReasons"] as! [String]).isEmpty, "lost descendant failure diagnostics")
        precondition(partial["rootDiscoveryComplete"] as? Bool == true,
            "attached-child failure vetoed complete independent-root discovery")
        scene.declarationUnavailable = true
        let rootsUnknown = try scene.collect()
        precondition(rootsUnknown["rootDiscoveryComplete"] as? Bool == false,
            "AXWindows failure claimed independent roots were complete")
        precondition(!(rootsUnknown["surfaces"] as! [[String: Any]]).isEmpty,
            "unknown root coverage erased addressable windows")
        let unparented = Scene()
        unparented.declared.append(unparented.node(kAXWindowRole))
        let unknownParent = try unparented.collect()
        precondition(unknownParent["rootDiscoveryComplete"] as? Bool == false,
            "unknown parent relationship claimed complete roots")
        let noTrust = Scene()
        noTrust.trusted = false
        let permissionUnknown = try noTrust.collect()
        precondition(permissionUnknown["rootDiscoveryComplete"] as? Bool == false)
    }


    @MainActor static func visualBatchesRetainProfileFailuresAndDeadline() throws {
        let scene = Scene()
        let execution = NativeExecution(state: scene.state, session: "visual-scene", cancelActive: {})
        execution.directory = scene.directory
        let target: [String: Any] = ["session": "visual-scene", "instance": NSNull(),
            "rootWindow": NSNull(), "surface": NSNull(), "node": NSNull(), "capture": NSNull()]
        func collect(_ profile: String?, _ applications: [String]) throws -> [String: Any] {
            let intent = try NativeIntent(["kind": "read", "parameters": [:]], mode: "read")
            let op = NativeOperation(timeoutMs: 30000, writable: false, intent: intent, target: target,
                now: { scene.elapsed }, release: {}, cancelActive: {})
            defer { _ = op.finish() }
            var query: [String: Any] = ["kind": "surfaceBatch", "applications": applications]
            if let profile { query["profile"] = profile }
            return try ReadPipeline.collect(["operation": op.token, "target": target, "query": query],
                operation: { _ in op }, access: execution.queryAccess)
        }
        scene.failedApplications.insert("instance:gap")
        let names = ["instance:first", "instance:gap", "instance:last"]
        let result = try collect("visual", names)
        let entries = result["entries"] as! [[String: Any]]
        precondition(entries.map { $0["application"] as! String } == names)
        precondition(entries[1]["result"] == nil && entries[1]["error"] != nil)
        for index in [0, 2] {
            let directory = entries[index]["result"] as! [String: Any]
            precondition(directory["application"] as? String == names[index])
            let rows = directory["surfaces"] as! [[String: Any]]
            precondition(rows.count == 1 && rows[0]["node"] is NSNull)
        }
        precondition(scene.content.isEmpty && scene.activation == 0,
            "visual batch initialized a provider or entered control contents")
        let legacy = try collect(nil, ["instance:first"])["entries"] as! [[String: Any]]
        precondition(legacy[0]["error"] != nil && scene.activation == 1 && !scene.content.isEmpty,
            "default batch no longer preserves accessibility collection")
        scene.locations.removeAll()
        scene.onLocate = { scene.elapsed += 31 }
        do {
            _ = try collect("visual", ["instance:first", "instance:last"])
            preconditionFailure("batch restarted its operation deadline")
        } catch let error as DriverError {
            precondition(error.code == .executionFailed)
        }
        precondition(scene.locations == ["instance:first"], "expired batch continued to another application")
    }

    @MainActor static func visualWindowFacts() throws {
        let scene = Scene()
        let other = scene.node(kAXWindowRole, parent: scene.application, nativeID: 207)
        let sheet = scene.node(kAXSheetRole, parent: scene.window)
        let popover = scene.node("AXPopover", parent: scene.window)
        let unknown = scene.node(kAXWindowRole)
        scene.put(other, kAXModalAttribute, kCFBooleanFalse)
        scene.put(popover, kAXModalAttribute, kCFBooleanFalse)
        scene.put(scene.window, "AXSheets", [sheet] as CFArray)
        scene.children[CFHash(scene.window)] = [scene.files, popover]
        scene.declared += [other, unknown]
        let value = try scene.collect()
        let rows = value["surfaces"] as! [[String: Any]]
        func row(_ element: AXUIElement) -> [String: Any] {
            rows.first { record in
                let retained = scene.directory.surface(record["surface"] as! String)?.element
                return retained.map { CFEqual($0, element) } ?? false
            }!
        }
        let root = row(scene.window)
        precondition(row(sheet)["parent"] as? String == root["window"] as? String)
        precondition(row(sheet)["modal"] as? Bool == true, "AXSheet relation must remain positive modality")
        precondition(row(popover)["parent"] as? String == root["window"] as? String)
        precondition(row(unknown)["parentState"] as? String == "unknown")
        precondition(row(unknown)["modal"] is NSNull, "unknown modality became false")
        precondition(row(other)["parentState"] as? String == "none")
        precondition(scene.content.isEmpty && scene.activation == 0, "window facts entered contents")
        scene.put(sheet, kAXModalAttribute, kCFBooleanFalse)
        let fresh = try scene.collect()["surfaces"] as! [[String: Any]]
        precondition(fresh.first { $0["window"] as? String == row(sheet)["window"] as? String }?["modal"] as? Bool == false)
    }

    @MainActor static func nativeOnlyRefreshRetainsBinding() throws {
        let scene = Scene()
        let initial = try scene.collect()["surfaces"] as! [[String: Any]], original = initial[0]
        let ref = original["window"] as! String, scope = original["surface"] as! String
        scene.trusted = false
        let current = try scene.collect()["surfaces"] as! [[String: Any]]
        precondition(current.count == 1 && current[0]["window"] as? String == ref)
        precondition(current[0]["parentState"] as? String == "unknown", "no AX parent became independent root")
        precondition(current[0]["modal"] is NSNull)
        precondition(scene.directory.surface(scope)?.element.map { CFEqual($0, scene.window) } == true,
                     "native-only refresh erased original AX binding")
        let captured = try scene.directory.windowScope(instance: original["instance"] as! String, root: ref, surface: scope)
        precondition(captured.surface.native?.mapping.id == 380 && scene.content.isEmpty && scene.activation == 0)
        scene.trusted = true; scene.nativeAvailable = false
        let axOnly = try scene.collect()["surfaces"] as! [[String: Any]]
        precondition(axOnly.count == 1 && axOnly[0]["node"] is NSNull)
        let reasons = try scene.collect()["stopReasons"] as! [String]
        precondition(!reasons.isEmpty)
        precondition(scene.content.isEmpty)
    }

    @MainActor static func hiddenAndUnknownNativeLifetimesCanReappear() throws {
        for unavailable in [false, true] {
            let scene = Scene()
            scene.trusted = false
            let original = (try scene.collect()["surfaces"] as! [[String: Any]])[0]
            let scope = original["surface"] as! String
            scene.nativeIDs = []
            scene.hiddenNativeIDs = [380]
            scene.nativePresenceUnavailable = unavailable
            let absent = try scene.collect()
            precondition((absent["surfaces"] as! [[String: Any]]).isEmpty)
            precondition((absent["endedWindows"] as! [[String: Any]]).isEmpty,
                         "hidden or unavailable WindowServer evidence retired an existing native lifetime")
            precondition(scene.directory.surface(scope) != nil)
            scene.nativeIDs = [380]
            scene.hiddenNativeIDs = []
            scene.nativePresenceUnavailable = false
            let reappeared = (try scene.collect()["surfaces"] as! [[String: Any]])[0]
            for key in ["window", "surface", "instance"] {
                precondition(original[key] as? String == reappeared[key] as? String,
                             "reappearing native window changed its exact binding")
            }
        }
    }

    @MainActor static func exactNativeHintKeepsIdentityAcrossOnscreenProjectionAndAXProxyChanges() throws {
        let scene = Scene()
        scene.nativeIDs = [380, 381]
        scene.rootRegistration = { _, _ in "current-root" }
        let original = (try scene.collect(nil)["surfaces"] as! [[String: Any]]).first {
            $0["node"] as? String == "current-root"
        }!
        // Qt can retain the NSWindow while replacing its AX proxy on reopen.
        // The native list is an earlier onscreen projection; another window
        // still proves this same process incarnation without listing the popup.
        let replacement = scene.node(kAXWindowRole)
        scene.ids[CFHash(replacement)] = 380
        scene.declared = [replacement]
        scene.focused = replacement
        scene.inputWindow = replacement
        scene.focusedElement = nil
        scene.nativeIDs = [381]
        scene.hiddenNativeIDs = [380]
        let beforeNativeProjection = try scene.collect(nil)
        let current = (beforeNativeProjection["surfaces"] as! [[String: Any]]).first {
            $0["node"] as? String == "current-root"
        }!
        for key in ["window", "surface", "instance"] {
            precondition(current[key] as? String == original[key] as? String,
                         "a fresh AX proxy changed \(key); original=\(original), current=\(current), directory=\(beforeNativeProjection)")
        }
        precondition(current["nativeWindow"] as? Bool == false && current["accessibility"] as? Bool == true,
                     "identity continuity must not invent current native visibility")
        precondition((beforeNativeProjection["endedWindows"] as! [[String: Any]]).isEmpty)
        let retained = scene.directory.surface(current["surface"] as! String)!
        precondition(retained.element.map { CFEqual($0, replacement) } == true,
                     "the fixed physical identity must expose the current exact AX proxy")
        precondition(retained.native?.mapping.id == 380,
                     "a filtered native projection erased the retained positive physical identity")
        scene.nativeIDs = [380, 381]
        scene.hiddenNativeIDs = []
        let afterNativeProjection = (try scene.collect(nil)["surfaces"] as! [[String: Any]]).first {
            $0["node"] as? String == "current-root"
        }!
        for key in ["window", "surface", "instance"] {
            precondition(afterNativeProjection[key] as? String == current[key] as? String,
                         "the later onscreen native projection changed an already published fixed binding")
        }
        precondition(afterNativeProjection["nativeWindow"] as? Bool == true)
    }

    @MainActor static func duplicateNativeHintsDoNotMergeIndependentAXRoots() throws {
        let scene = Scene()
        scene.nativeIDs = [380, 381]
        scene.rootRegistration = { element, _ in String(CFHash(element)) }
        let original = (try scene.collect(nil)["surfaces"] as! [[String: Any]]).first {
            $0["node"] as? String == String(CFHash(scene.window))
        }!
        let first = scene.node(kAXWindowRole), second = scene.node(kAXWindowRole)
        scene.ids[CFHash(first)] = 380
        scene.ids[CFHash(second)] = 380
        scene.declared = [first, second]
        scene.focused = nil
        scene.inputWindow = nil
        scene.focusedElement = nil
        scene.nativeIDs = [381]
        scene.hiddenNativeIDs = [380]
        let roots = (try scene.collect(nil)["surfaces"] as! [[String: Any]]).filter { $0["accessibility"] as? Bool == true }
        precondition(roots.count == 2 && Set(roots.compactMap { $0["window"] as? String }).count == 2,
                     "a shared CGID hint cannot merge independent logical AX roots")
        precondition(roots.allSatisfy { $0["window"] as? String != original["window"] as? String },
                     "conflicting native hints cannot adopt the previously known physical reference")
    }

    @MainActor static func unprovenExactHintDoesNotAllocateAnUnownedPhysicalLifetime() throws {
        let scene = Scene()
        scene.nativeIDs = [381]
        scene.rootRegistration = { _, _ in "current-root" }
        let original = (try scene.collect(nil)["surfaces"] as! [[String: Any]]).first {
            $0["node"] as? String == "current-root"
        }!
        precondition(original["nativeWindow"] as? Bool == false)
        // This AX-only object vanishes before any native projection proves
        // its physical lifetime. The same numeric hint on another AX object
        // cannot by itself revive or rebind that first public reference.
        scene.declared = []
        scene.inputWindow = nil
        scene.focused = nil
        scene.focusedElement = nil
        _ = try scene.collect(nil)
        let replacement = scene.node(kAXWindowRole)
        scene.ids[CFHash(replacement)] = 380
        scene.declared = [replacement]
        scene.inputWindow = replacement
        scene.focused = replacement
        let current = (try scene.collect(nil)["surfaces"] as! [[String: Any]]).first {
            $0["node"] as? String == "current-root"
        }!
        for key in ["window", "surface", "instance"] {
            precondition(current[key] as? String != original[key] as? String,
                         "an unproven CGID allocated an unowned physical key and rebound the first AX-only Window")
        }
        scene.nativeIDs = [380, 381]
        let projected = (try scene.collect(nil)["surfaces"] as! [[String: Any]]).first {
            $0["node"] as? String == "current-root"
        }!
        for key in ["window", "surface", "instance"] {
            precondition(projected[key] as? String == current[key] as? String,
                         "a first positive native match must retain the current AX object's original alias")
        }
    }

    @MainActor static func endedNativeHintGetsNewIdentityBeforeNativeProjection() throws {
        let scene = Scene()
        scene.nativeIDs = [380, 381]
        scene.rootRegistration = { _, _ in "current-root" }
        let original = (try scene.collect(nil)["surfaces"] as! [[String: Any]]).first {
            $0["node"] as? String == "current-root"
        }!
        scene.declared = []
        scene.focused = nil
        scene.inputWindow = nil
        scene.focusedElement = nil
        scene.nativeIDs = [381]
        let ended = try scene.collect(nil)
        precondition((ended["endedWindows"] as! [[String: Any]]).contains {
            $0["window"] as? String == original["window"] as? String
        })
        let replacement = scene.node(kAXWindowRole)
        scene.ids[CFHash(replacement)] = 380
        scene.declared = [replacement]
        scene.focused = replacement
        scene.inputWindow = replacement
        // The new lifetime reports an exact CGID before the native projection
        // lists it. A prior tombstone cannot be undone by that same numeric ID.
        let current = (try scene.collect(nil)["surfaces"] as! [[String: Any]]).first {
            $0["node"] as? String == "current-root"
        }!
        for key in ["window", "surface", "instance"] {
            precondition(current[key] as? String != original[key] as? String,
                         "a fresh reported CGID resurrected an authoritatively ended generation")
        }
        scene.nativeIDs = [380, 381]
        let projected = (try scene.collect(nil)["surfaces"] as! [[String: Any]]).first {
            $0["node"] as? String == "current-root"
        }!
        for key in ["window", "surface", "instance"] {
            precondition(projected[key] as? String == current[key] as? String,
                         "the later native projection must preserve the new exact generation")
        }
        let references = ApplicationElements()
        let process = scene.process
        let next = NativeProcessIdentity(pid: process.pid, parent: 0, seconds: 2, microseconds: 0,
                                         executable: "/test-window-provider")
        let first = references.windowReference(nativeID: 380, process: process, owner: scene.app, element: replacement)
        let second = references.windowReference(nativeID: 380, process: next, owner: scene.app, element: replacement)
        precondition(first != second, "an exact CGID cannot alias distinct process incarnations")
    }

    @MainActor static func endedNativeLifetimeNeverReusesWindowOrScope() throws {
        let scene = Scene()
        scene.trusted = false
        let original = (try scene.collect()["surfaces"] as! [[String: Any]])[0]
        scene.nativeIDs = []
        let ended = try scene.collect()
        let tombstones = ended["endedWindows"] as! [[String: Any]]
        precondition(tombstones.count == 1)
        for key in ["window", "surface", "instance"] {
            precondition(tombstones[0][key] as? String == original[key] as? String)
        }
        precondition(scene.directory.surface(original["surface"] as! String) == nil)
        // A WindowServer numeric slot may be reused in the same process. It
        // cannot inherit a positively ended SDK/native lifetime or scope.
        scene.nativeIDs = [380]
        let recreated = try scene.collect()
        let fresh = (recreated["surfaces"] as! [[String: Any]])[0]
        for key in ["window", "surface", "instance"] {
            precondition(fresh[key] as? String != original[key] as? String,
                         "new native lifetime rebound an ended reference")
        }
        precondition((recreated["endedWindows"] as! [[String: Any]]).count == 1,
                     "later directory lost positive end evidence consumed by an earlier read")
    }

    @MainActor static func staleRootRegistrationKeepsOtherWindowsAndNativeVisualBinding() throws {
        for nativeStillPresent in [true, false] {
            let scene = Scene()
            let healthy = scene.node(kAXWindowRole, parent: scene.application, nativeID: 207)
            scene.declared = [scene.window, healthy]
            let initial = try scene.collect()["surfaces"] as! [[String: Any]]
            let original = initial.first { row in
                scene.directory.surface(row["surface"] as! String)?.native?.mapping.id == 380
            }!
            let healthyRow = initial.first { row in
                scene.directory.surface(row["surface"] as! String)?.native?.mapping.id == 207
            }!
            var attempted = [CFHashCode]()
            scene.rootRegistration = { element, _ in
                attempted.append(CFHash(element))
                if CFEqual(element, scene.window) {
                    if !nativeStillPresent { scene.nativeIDs.removeAll { $0 == 380 } }
                    throw DriverError("native node no longer exists", code: .staleTarget)
                }
                return "healthy-root"
            }
            let result = try scene.collect(nil)
            let rows = result["surfaces"] as! [[String: Any]]
            precondition(attempted.contains(CFHash(scene.window)) && attempted.contains(CFHash(healthy)))
            let surviving = rows.first { $0["window"] as? String == healthyRow["window"] as? String }!
            precondition(surviving["node"] as? String == "healthy-root" && surviving["accessibility"] as? Bool == true,
                         "one stale root hid an unrelated healthy accessibility window")
            let affected = rows.first { $0["window"] as? String == original["window"] as? String }
            if nativeStillPresent {
                precondition(affected != nil && affected?["node"] is NSNull && affected?["accessibility"] as? Bool == false)
                precondition(affected?["surface"] as? String == original["surface"] as? String)
                precondition(affected?["instance"] as? String == original["instance"] as? String)
                let scope = scene.directory.surface(original["surface"] as! String)
                precondition(scope?.native?.mapping.id == 380 && scope?.element == nil,
                             "native fallback retained an invalid AX proxy or rebound the visual window")
            } else {
                precondition(affected == nil, "a closed native window was republished from an earlier catalogue")
                precondition(result["rootDiscoveryComplete"] as? Bool == false)
            }
            let gaps = result["stopReasons"] as! [String]
            precondition(gaps.contains { $0.contains("AX root unavailable for window") && $0.contains("stale_target") })
            precondition(!gaps.contains("surface catalogue entry limit"), "a skipped stale root became a false entry-limit gap")
        }
    }

    @MainActor static func staleAXOnlyRootIsOmittedWithoutHidingHealthyWindows() throws {
        let scene = Scene()
        let ghost = scene.node(kAXWindowRole, parent: scene.application)
        scene.declared = [scene.window, ghost]
        let initial = try scene.collect()["surfaces"] as! [[String: Any]]
        let ghostRow = initial.first { row in
            scene.directory.surface(row["surface"] as! String)?.element.map { CFEqual($0, ghost) } == true
        }!
        scene.rootRegistration = { element, _ in
            if CFEqual(element, ghost) { throw DriverError("native node no longer exists", code: .staleTarget) }
            return "healthy-root"
        }
        let result = try scene.collect(nil)
        let rows = result["surfaces"] as! [[String: Any]]
        precondition(rows.count == 1 && rows[0]["node"] as? String == "healthy-root")
        precondition(!rows.contains { $0["window"] as? String == ghostRow["window"] as? String },
                     "an invalid AX-only root was advertised as a live window")
        precondition(result["rootDiscoveryComplete"] as? Bool == false)
        precondition((result["endedWindows"] as! [[String: Any]]).isEmpty,
                     "a local AX registration failure invented permanent lifetime end evidence")
    }

    @MainActor static func rootRegistrationCancellationAndOwnerFailureRemainFatal() throws {
        for failure in 0..<3 {
            let scene = Scene()
            scene.rootRegistration = { _, _ in
                if failure == 0 { throw CancellationError() }
                if failure == 1 { throw RequestDeadlineExceeded() }
                throw DriverError("operation expired", code: .executionFailed)
            }
            do { _ = try scene.collect(nil); preconditionFailure("fatal root registration failure became a partial catalogue") }
            catch is CancellationError { precondition(failure == 0) }
            catch is RequestDeadlineExceeded { precondition(failure == 1) }
            catch let error as DriverError { precondition(failure == 2 && error.code == .executionFailed) }
        }
        let scene = Scene()
        scene.rootRegistration = { _, _ in
            scene.ownerFailure = DriverError("application lifetime ended", code: .staleTarget)
            throw DriverError("native node no longer exists", code: .staleTarget)
        }
        do { _ = try scene.collect(nil); preconditionFailure("ended application became a partial catalogue") }
        catch let error as DriverError { precondition(error.description == "application lifetime ended") }
    }

    @MainActor static func legacyContentPositiveControl() throws {
        let scene = Scene()
        do { _ = try scene.collect(nil); preconditionFailure("legacy tree query avoided original content poison") }
        catch let error as DriverError { precondition(error.code == .observationIncomplete) }
        precondition(scene.activation == 1)
        precondition(scene.content.contains("AXFocusedUIElement") && scene.content.contains("file-list.AXChildren poison"))
    }

    @MainActor static func declaredControlRootDoesNotOpenItsContents() throws {
        let scene = Scene()
        scene.declared = [scene.files]; scene.focused = nil
        let rows = try scene.collect()["surfaces"] as! [[String: Any]]
        precondition(scene.content.isEmpty && scene.activation == 0, "declared inline root entered control contents")
        precondition(rows.contains { $0["kind"] as? String == "unknown" && $0["node"] is NSNull })
        precondition(rows.contains { row in
            scene.directory.surface(row["surface"] as! String)?.native?.mapping.id == 380
        }, "physical window disappeared behind an unsupported declared control root")
    }
}
