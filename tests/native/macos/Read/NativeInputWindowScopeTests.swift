// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices

@main struct NativeInputWindowScopeTests {
    @MainActor final class Scene {
        let app = NSRunningApplication.current
        let application = AXUIElementCreateApplication(9000)
        let open = AXUIElementCreateApplication(9001)
        let other = AXUIElementCreateApplication(9002)
        var attributes = [CFHashCode: [String: CFTypeRef]]()
        var children = [CFHashCode: [AXUIElement]]()
        var ids = [CFHashCode: CGWindowID]()
        var nativeIDs: [CGWindowID] = [380,207]
        var nativeParents = [CGWindowID:CGWindowID]()
        var unknownNativeParents = Set<CGWindowID>()
        var nativeProcesses = [CGWindowID:NativeProcessIdentity]()
        var currentProcesses = [pid_t:NativeProcessIdentity]()
        var unavailableProcesses = Set<pid_t>(), goneProcesses = Set<pid_t>()
        var nativePresence = [CGWindowID:WindowServerInfo.Presence]()
        var pidReads = [CFHashCode:Int](), pidLimit = [CFHashCode:Int]()
        var invalid = Set<CFHashCode>()
        var elapsed = 100.0
        var calls = [String: Int]()
        var deepCount = 0
        var live = true
        var nativeIdentity = true
        var trusted = true
        var onCount: ((AXUIElement) -> Void)?
        var focused: AXUIElement?
        var declared = [AXUIElement]()
        var nextID: pid_t = 10000
        var rootRef = ""
        var otherRef = ""
        var scope = ""
        var instance = ""
        var beforeScope: (() -> Void)?
        lazy var access = NativeApplicationAccess(windowQuery: api())
        // A CLI test has no LaunchServices application registration. Its owner
        // is an opaque NSRunningApplication token; all OS leaf adapters below
        // are scripted, including lifetime validation. No app is launched.
        lazy var state = NativeDesktopState(applications: access, appSelections: AppSelections(validateOwner: { _ in }))
        lazy var directory: NativeDirectory = {
            var services = NativeDirectory.WindowServices()
            services.locate = { _ in self.app }
            services.listIfAvailable = { _ in self.windows() }
            services.trusted = { self.trusted }
            services.focusedWindow = { _ in self.focused }
            services.list = { _ in self.windows() }
            services.owner = { _ in }
            services.process = { pid in
                guard self.nativeIdentity, !self.unavailableProcesses.contains(pid) else { return nil }
                return self.currentProcesses[pid] ?? self.windows().first { $0.process.pid == pid }?.process
            }
            services.info = { id in
                self.live ? self.info(id) : nil
            }
            services.presence = { id in self.nativePresence[id] ?? (self.nativeIDs.contains(id) ? .present(self.info(id)) : .absent) }
            services.isGone = { self.goneProcesses.contains($0) }
            services.role = { self.value($0,kAXRoleAttribute) as? String }
            return NativeDirectory(state: state, session: "test-session", windowServices: services)
        }()

        init() {
            put(application, kAXRoleAttribute, kAXApplicationRole as CFString)
            for (node, id, title) in [(open, CGWindowID(380), "Open"), (other, CGWindowID(207), "Main")] {
                put(node, kAXRoleAttribute, kAXWindowRole as CFString)
                put(node, kAXSubroleAttribute, kAXStandardWindowSubrole as CFString)
                put(node, kAXTitleAttribute, title as CFString)
                put(node, kAXModalAttribute, kCFBooleanFalse)
                put(node, kAXParentAttribute, application)
                put(node, "AXSheets", [] as CFArray)
                ids[CFHash(node)] = id
            }
            let split = node("AXSplitGroup", parent: open)
            let browser = node("AXBrowser", parent: split)
            let list = node("AXList", parent: browser)
            children[CFHash(open)] = [split]
            children[CFHash(split)] = [browser]
            children[CFHash(browser)] = [list]
            let groups = (0..<700).map { _ in node("AXGroup", parent: list) }
            children[CFHash(list)] = groups
            for group in groups {
                children[CFHash(group)] = [node("AXImage", parent: group), node("AXTextField", parent: group)]
            }
            put(open, kAXModalAttribute, kCFBooleanTrue)
            put(open, kAXSubroleAttribute, "AXDialog" as CFString)
            children[CFHash(other)] = []
            declared = [open, other]
            focused = open
        }

        func put(_ node: AXUIElement, _ key: String, _ value: CFTypeRef) {
            attributes[CFHash(node), default: [:]][key] = value
        }
        func node(_ role: String, parent: AXUIElement?) -> AXUIElement {
            defer { nextID += 1 }
            let value = AXUIElementCreateApplication(nextID)
            put(value, kAXRoleAttribute, role as CFString)
            if let parent { put(value, kAXParentAttribute, parent) }
            return value
        }
        func value(_ node: AXUIElement, _ key: String) -> CFTypeRef? {
            calls[key, default: 0] += 1
            elapsed += 0.00004
            if CFEqual(node, application) {
                if key == kAXWindowsAttribute { return declared as CFArray }
                if key == kAXFocusedWindowAttribute || key == kAXFocusedUIElementAttribute { return focused }
                if key == kAXMainWindowAttribute { return other }
            }
            return attributes[CFHash(node)]?[key]
        }
        func api() -> NativeWindowQueryAPI {
            var api = NativeWindowQueryAPI(attribute: { self.value($0, $1) })
            api.trusted = { self.trusted }
            api.application = { _ in self.application }
            api.read = { node, key in
                if self.invalid.contains(CFHash(node)) { return .invalid }
                return self.value(node, key).map(AXRead.value) ?? .unsupported
            }
            api.element = { node, key in
                guard let raw = self.value(node, key), CFGetTypeID(raw) == AXUIElementGetTypeID() else { return nil }
                return unsafeDowncast(raw, to: AXUIElement.self)
            }
            api.pid = { node in
                let key=CFHash(node)
                self.pidReads[key,default:0] += 1
                if let limit=self.pidLimit[key],self.pidReads[key,default:0] > limit { return (.invalidUIElement,0) }
                return (.success,self.app.processIdentifier)
            }
            api.inputWindow = { _, _, _ in self.focused }
            api.presentedWindow = { node, _, _ in node }
            api.nativeID = { self.ids[CFHash($0)] }
            api.hint = { node, _ in
                self.ids[CFHash(node)].map { WindowSelection.Hint(frame: self.frame($0), title: "", id: $0) }
            }
            api.relations.count = { node, key in
                self.onCount?(node)
                self.calls["count", default: 0] += 1
                self.elapsed += 0.00055
                if self.attributes[CFHash(node)]?[kAXRoleAttribute] as? String == "AXGroup" { self.deepCount += 1 }
                return (.success, self.children[CFHash(node)]?.count ?? 0)
            }
            api.relations.range = { node, _, count in
                self.calls["range", default: 0] += 1
                self.elapsed += 0.00018
                return (.success, Array((self.children[CFHash(node)] ?? []).prefix(count)) as CFArray)
            }
            api.relations.single = { _, _ in preconditionFailure("scripted supported arrays must not fall back") }
            return api
        }
        func frame(_ id: CGWindowID) -> CGRect { id == 380 ? CGRect(x: 524, y: 257, width: 880, height: 448) : CGRect(x: 10, y: 10, width: 400, height: 300) }
        func info(_ id: CGWindowID) -> [String:Any] {
            [kCGWindowNumber as String:id,kCGWindowOwnerPID as String:nativeProcesses[id]?.pid ?? app.processIdentifier]
        }
        func windows() -> [NativeWindows.Window] {
            let process = NativeProcessIdentity(pid: app.processIdentifier, parent: 0, seconds: 1, microseconds: 0,
                                                executable: "/opaque-test-provider")
            return nativeIDs.map { id in
                let owner=nativeProcesses[id] ?? process
                return NativeWindows.Window(application: app, process: owner,
                    mapping: .init(id: id, pid: owner.pid, frame: frame(id), title: nil, width: Int(frame(id).width), height: Int(frame(id).height)),
                    onScreen: true, parentID: unknownNativeParents.contains(id) ? nil : nativeParents[id] ?? 0)
            }
        }
        func bind(root: AXUIElement? = nil, nativeID: CGWindowID = 380) {
            let root = root ?? open
            let native = windows().first { $0.mapping.id == nativeID }!
            rootRef = access.windowReference(native, element: root)
            otherRef = access.windowReference(windows()[1], element: other)
            scope = access.nativeScope(native, owner: app)
            instance = state.appSelections.get(owner: app, root: rootRef).id
            directory.remember(.init(owner: app, window: rootRef, scope: scope, parent: nil, element: root, native: native))
        }
        func boundary(legacy: Bool = false) throws -> [String] {
            beforeScope?()
            if !legacy { return try directory.inputWindowScope(instance: instance, root: rootRef, surface: scope).blocked }
            // Baseline control: the exact catalogue path previously selected by
            // production inputWindowScope; same provider and scope/blocker logic.
            let rows = try access.windows(app, nativeWindows: windows(), contentRoot: open, purpose: .inputValidation)
            return try NativeWindowBoundary.blocked(root: rootRef, target: rootRef,
                windows: rows.map { .init(ref: $0.ref, parent: $0.parent, modal: $0.modal == true) },
                focused: access.focusedWindow(app, windows: rows))
        }
        func rememberModal(_ modal: AXUIElement) throws {
            // Simulate the same identity/evidence retained by an observation,
            // then remove this temporary explicit edge before the input query.
            put(open,"AXSheets",[modal] as CFArray)
            let graph = try access.inputBoundary(app,nativeWindows:windows(),contentRoot:open,required:[open,modal])
            let ref = graph.windows.first { CFEqual($0.element,modal) }!.ref
            let surface = access.observationScope(app,window:modal,reference:ref)
            directory.remember(.init(owner:app,window:ref,scope:surface,parent:rootRef,element:modal,native:nil,
                                     queryHint:.observedModal))
            put(open,"AXSheets",[] as CFArray)
        }
        func uncertainModalOwner() throws -> (modal: AXUIElement,owner: AXUIElement) {
            let modal = node(kAXSheetRole,parent:open)
            put(modal,kAXModalAttribute,kCFBooleanTrue);put(modal,"AXSheets",[] as CFArray)
            try rememberModal(modal)
            let owner = node(kAXWindowRole,parent:nil)
            put(owner,kAXModalAttribute,kCFBooleanFalse);put(owner,"AXSheets",[modal] as CFArray)
            put(modal,kAXParentAttribute,owner)
            declared.append(owner)
            return (modal,owner)
        }
        func rememberCGModal(_ modal: AXUIElement, id: CGWindowID = 501, process: NativeProcessIdentity? = nil)
            -> (ref:String,scope:String,native:NativeWindows.Window)
        {
            ids[CFHash(modal)]=id
            if !nativeIDs.contains(id) { nativeIDs.append(id) }
            if let process { nativeProcesses[id]=process;currentProcesses[process.pid]=process }
            let native=windows().first { $0.mapping.id==id }!
            let ref=access.windowReference(native,element:modal)
            let scope=access.observationScope(app,window:modal,reference:ref)
            directory.remember(.init(owner:app,window:ref,scope:scope,parent:rootRef,element:modal,native:native,
                                     queryHint:.observedModal))
            return(ref,scope,native)
        }
    }

    static func expect(_ condition: Bool, file: StaticString = #fileID, line: UInt = #line) { precondition(condition, file: file, line: line) }

    @MainActor static func main() async throws {
        let old = try await drag(legacy: true)
        expect(old.dispatch == "partial" && old.events > 2 && old.events < 63 && !old.endpoint)
        let fixed = try await drag(legacy: false)
        print("baseline=\(old) inputBoundary=\(fixed)")
        expect(fixed.dispatch == "dispatched" && fixed.events == 63 && fixed.endpoint)
        expect(fixed.deep == 0 && old.deep > 10000)
        try frontierAndModalChanges()
        try requiredSurfaceAndOwnership()
        try originalOwnerBoundaries()
        try uncertainFacts()
        try observedModalHints()
        try cgModalDeepRegression()
        try cgModalBoundaries()
        try cgModalNativeOtherDocumentRegression()
        try visualDirectoryBoundaryCombinations()
        try modalOwnerAncestry()
        for kind in ["unknownOwnerBefore","cgModalBefore","cgUnknownBefore","cgNativeTargetBefore"] {
            let refused = try await drag(legacy:false,change:kind)
            expect(refused.dispatch == "notDispatched" && refused.events == 0 && !refused.endpoint)
        }
        let ownVisual = try await drag(legacy:false,change:"ownModalUnavailable")
        expect(ownVisual.dispatch == "dispatched" && ownVisual.events == 63 && ownVisual.endpoint)
        let unrelated = try await drag(legacy:false,change:"cgUnrelated")
        expect(unrelated.dispatch == "dispatched" && unrelated.events == 63 && unrelated.endpoint)
        for change in ["sheet", "appModal", "knownDeepModal", "unknownModalAncestor", "cgModal", "cgUnknown", "cgReparent", "cgOwnerUnknown", "identity", "unknownSheet", "cancel"] {
            // Cancellation remains confined to this original gesture's task.
            let stopped = try await Task { @MainActor in try await drag(legacy: false, change: change) }.value
            expect(stopped.dispatch == "partial" && stopped.events == 4 && !stopped.endpoint)
        }
        print("PASS production inputWindowScope + real collector/event/receipt/ledger: old partial, boundary63 endpoint; fresh sheets/modals/identity and noReplay")
    }

    @MainActor static func visualDirectoryBoundaryCombinations() throws {
        func refresh(_ s: Scene) throws {
            let deep = s.deepCount
            let value = try s.directory.collectSurfaces("instance:visual-scene", profile: .visual)
            let rows = value["surfaces"] as! [[String: Any]]
            expect(rows.allSatisfy { $0["node"] is NSNull && $0["accessibility"] as? Bool == false })
            expect(s.deepCount == deep)
        }
        func refused(_ s: Scene) throws {
            do { _ = try s.boundary(); preconditionFailure("visual refresh erased necessary modal uncertainty") }
            catch let error as DriverError { expect(error.code == .inputUnavailable) }
        }
        let ax = Scene(); ax.bind()
        let sheet = ax.node(kAXSheetRole, parent: ax.open)
        ax.put(sheet,kAXModalAttribute,kCFBooleanTrue);ax.put(sheet,"AXSheets",[] as CFArray)
        try ax.rememberModal(sheet)
        let anchor = ax.node("AXGroup", parent: ax.open)
        ax.children[CFHash(ax.open)] = [anchor]; ax.children[CFHash(anchor)] = [sheet]
        ax.put(sheet,kAXParentAttribute,anchor)
        try refresh(ax); expect(!(try ax.boundary()).isEmpty)
        ax.attributes[CFHash(sheet)]?.removeValue(forKey:kAXModalAttribute)
        ax.put(sheet,kAXRoleAttribute,kAXWindowRole as CFString)
        try refresh(ax); try refused(ax)
        ax.put(sheet,kAXModalAttribute,kCFBooleanFalse)
        try refresh(ax); expect(try ax.boundary().isEmpty)

        let cg = Scene(); cg.bind()
        let modal = cg.node(kAXWindowRole,parent:cg.open)
        cg.put(modal,kAXModalAttribute,kCFBooleanTrue);cg.put(modal,"AXSheets",[] as CFArray)
        let original = cg.rememberCGModal(modal)
        cg.invalid.insert(CFHash(modal))
        try refresh(cg); try refused(cg)
        // Failed AX declaration does not end the exact retained CG identity.
        guard case .retainedModal(let hint)? = cg.directory.surface(original.scope)?.queryHint else {
            preconditionFailure("visual directory discarded CG modal query hint")
        }
        expect(hint.native?.id == 501 && hint.native?.process == original.native.process)
        let proxy = cg.node(kAXWindowRole,parent:cg.open)
        cg.put(proxy,kAXModalAttribute,kCFBooleanFalse);cg.put(proxy,"AXSheets",[] as CFArray)
        cg.ids[CFHash(proxy)] = 501;cg.declared.append(proxy)
        try refresh(cg); expect(try cg.boundary().isEmpty)
        cg.put(proxy,kAXModalAttribute,kCFBooleanTrue)
        try refresh(cg); expect(!(try cg.boundary()).isEmpty)
        expect(CFEqual(cg.directory.surface(cg.scope)!.element!,cg.open))

        let native = Scene(); native.bind();native.trusted = false
        try refresh(native)
        expect(CFEqual(native.directory.surface(native.scope)!.element!,native.open))
        expect(try native.boundary().isEmpty) // own modal/native visual route remains usable
        native.trusted = true
        let blocker = native.node(kAXWindowRole,parent:native.open)
        native.put(blocker,kAXModalAttribute,kCFBooleanTrue);native.put(blocker,"AXSheets",[] as CFArray)
        _ = native.rememberCGModal(blocker);native.invalid.insert(CFHash(blocker))
        native.trusted = false
        try refresh(native);try refused(native)

        let other = Scene();other.bind()
        let foreign = other.node(kAXWindowRole,parent:other.other)
        other.put(foreign,kAXModalAttribute,kCFBooleanTrue);other.put(foreign,"AXSheets",[] as CFArray)
        _ = other.rememberCGModal(foreign)
        other.invalid.insert(CFHash(foreign));other.nativeParents[501] = 207
        try refresh(other);expect(try other.boundary().isEmpty)
        other.nativeParents[501] = 380
        try refresh(other);try refused(other)
        print("PASS visual directory -> existing capture/input boundary: AX-only/CG hints, unknown/false, native degradation, same native proxy and document owner")
    }

    @MainActor static func drag(legacy: Bool, change: String? = nil) async throws
        -> (dispatch: String, events: Int, endpoint: Bool, deep: Int)
    {
        let scene = Scene(); scene.bind()
        var knownDeepModal: AXUIElement?, knownAnchor: AXUIElement?, uncertainModal: AXUIElement?
        var cgModal: AXUIElement?
        let beforeInput=["unknownOwnerBefore","cgModalBefore","cgUnknownBefore","cgNativeTargetBefore"].contains(change ?? "")
        if ["cgModalBefore","cgUnknownBefore","cgModal","cgUnknown","cgUnrelated","cgReparent","cgOwnerUnknown","cgNativeTargetBefore"].contains(change ?? "") {
            let modal=scene.node(kAXWindowRole,parent:scene.open)
            scene.put(modal,kAXModalAttribute,kCFBooleanTrue);scene.put(modal,"AXSheets",[] as CFArray)
            _=scene.rememberCGModal(modal)
            let anchor=scene.node("AXGroup",parent:scene.open)
            scene.children[CFHash(scene.open)]=[anchor];scene.children[CFHash(anchor)]=[modal]
            scene.put(modal,kAXParentAttribute,anchor);cgModal=modal
            if ["cgUnrelated","cgReparent","cgOwnerUnknown","cgNativeTargetBefore"].contains(change ?? "") {
                scene.invalid.insert(CFHash(modal))
                scene.nativeParents[501] = change == "cgNativeTargetBefore" ? 380 : 207
            } else if change == "cgUnknownBefore" { scene.invalid.insert(CFHash(modal)) }
            else if change != "cgModalBefore" { scene.put(modal,kAXModalAttribute,kCFBooleanFalse) }
        }
        if change == "ownModalUnavailable" {
            let native=scene.windows()[0]
            scene.directory.remember(.init(owner:scene.app,window:scene.rootRef,scope:scene.scope,parent:nil,
                                          element:scene.open,native:native,queryHint:.observedModal))
        }
        if change == "unknownOwnerBefore" || change == "unknownModalAncestor" {
            let pair = try scene.uncertainModalOwner()
            uncertainModal=pair.modal
            if change == "unknownModalAncestor" { scene.put(pair.modal,kAXModalAttribute,kCFBooleanFalse) }
        }
        if change == "knownDeepModal" {
            let modal = scene.node(kAXSheetRole,parent:scene.open)
            scene.put(modal,kAXModalAttribute,kCFBooleanTrue);scene.put(modal,"AXSheets",[] as CFArray)
            try scene.rememberModal(modal)
            let anchor = scene.node("AXGroup",parent:scene.open)
            scene.children[CFHash(scene.open)] = [anchor]
            scene.put(modal,kAXParentAttribute,anchor)
            scene.put(modal,kAXModalAttribute,kCFBooleanFalse)
            knownDeepModal=modal;knownAnchor=anchor
        }
        let parameters: [String: Any] = ["fromX":285, "fromY":375, "toX":800, "toY":375]
        let intent = try NativeIntent(["kind":"drag", "parameters":parameters], mode: "write")
        let op = NativeOperation(timeoutMs: 30000, writable: true, intent: intent, target: [:], now: { scene.elapsed }, release: {}, cancelActive: {})
        let request = try NativePointerIntent(unit: "pointer_sequence", kind: "drag", parameters: parameters, foreground: false)
        let from = CGPoint(x:809,y:632), to = CGPoint(x:1324,y:632)
        let events = try NativePointerEvents(request: request, from: from, to: to, viewport: CGSize(width:880,height:448), source: CGEventSource(stateID:.privateState)!)
        var posted = [(CGEventType,CGPoint)](), hits = 0, bodies = 0
        func scope() throws {
            let blocked = try scene.boundary(legacy: legacy)
            guard blocked.isEmpty else { throw DriverError("live modal", code: .blockedByModal) }
            try op.check(write: true)
        }
        let receipt = try await op.execute(step: 1, payload: Data("same-drag".utf8)) {
            bodies += 1
            let receipt = NativeExecutionReceipt(target: [:], onEffect: {})
            do {
                try await NativeDispatchPipeline.run({
                    for _ in 0..<2 { try scope() }
                    try await events.deliver(check: { point, _ in
                        try scope()
                        let retained = NativeInputWindowHit.PublicWindow(id:380,pid:scene.app.processIdentifier,frame:scene.frame(380))
                        expect(NativeInputWindowHit.confirmsRetainedWindow(expected:retained,current:retained,at:point))
                        hits += 1
                        try scope()
                    }, post: { event, point in
                        posted.append((event.type,point))
                        guard event.type == .leftMouseDragged, posted.count == 3, let change else { return }
                        switch change {
                        case "identity": scene.nativeIdentity = false
                        case "cancel": withUnsafeCurrentTask { $0?.cancel() }
                        case "knownDeepModal":
                            scene.put(knownDeepModal!,kAXModalAttribute,kCFBooleanTrue)
                            scene.children[CFHash(knownAnchor!)] = [knownDeepModal!]
                        case "unknownModalAncestor": scene.put(uncertainModal!,kAXModalAttribute,kCFBooleanTrue)
                        case "cgModal": scene.put(cgModal!,kAXModalAttribute,kCFBooleanTrue)
                        case "cgUnknown": scene.invalid.insert(CFHash(cgModal!))
                        case "cgReparent": scene.nativeParents[501]=380
                        case "cgOwnerUnknown": scene.nativeParents[501]=999
                        case "cgUnrelated": break // this control keeps the same proven other-document owner
                        case "ownModalUnavailable": scene.trusted=false
                        default:
                            let modal = scene.node(change == "appModal" ? kAXWindowRole : kAXSheetRole,
                                                   parent: change == "unknownSheet" ? nil : change == "appModal" ? scene.application : scene.open)
                            scene.put(modal,"AXSheets",[] as CFArray)
                            if change != "unknownSheet" { scene.put(modal,kAXModalAttribute,kCFBooleanTrue) }
                            if change == "sheet" { scene.put(scene.open,"AXSheets",[modal] as CFArray) }
                            else { scene.declared.append(modal) }
                            if change == "appModal" { scene.focused = modal }
                        }
                    }, canRelease: { true }, skipped: receipt.recordCleanupUnknown,
                    onDispatch: { receipt.dispatch="unknown"; op.recordInputDispatch() }, onPosted: receipt.recordAccepted)
                    return false
                }, receipt: receipt)
            } catch { receipt.recordFailure(error) }
            return receipt.wire
        }
        expect(receipt["cleanup"] as? String == "complete")
        if beforeInput { expect(posted.isEmpty) }
        else { expect(posted.last?.0 == .leftMouseUp) }
        if !legacy && change == nil { expect(hits == 62 && scene.calls["count",default:0] < 1000) }
        if change != nil && !beforeInput { expect(posted.last?.1 == posted.dropLast().last?.1) }
        let count = posted.count
        _ = try await op.execute(step:1,payload:Data("same-drag".utf8)) { bodies += 1; return [:] }
        expect(bodies==1 && posted.count==count)
        if !beforeInput {
            do {
                _ = try await op.execute(step:2,payload:Data("another-unit".utf8)) { bodies += 1; return [:] }
                preconditionFailure("partial/dispatched input replayed")
            } catch {}
        }
        expect(bodies==1 && op.finish()=="complete")
        return (receipt["dispatch"] as! String, posted.count, posted.last?.1 == to, scene.deepCount)
    }

    @MainActor static func frontierAndModalChanges() throws {
        let scene = Scene(); scene.bind()
        expect(try scene.boundary().isEmpty)
        expect(scene.deepCount==0)
        // A later candidate's immediate surface must not be starved by Open's
        // file list. It is a child of the selected root, not another document.
        scene.put(scene.other,kAXParentAttribute,scene.open)
        let sheet = scene.node(kAXSheetRole,parent:scene.other)
        scene.put(sheet,kAXModalAttribute,kCFBooleanTrue)
        scene.put(sheet,"AXSheets",[] as CFArray)
        scene.children[CFHash(scene.other)]=[sheet]
        expect(!(try scene.boundary()).isEmpty)
        scene.children[CFHash(scene.other)]=[]
        scene.put(scene.open,"AXSheets",[sheet] as CFArray)
        scene.put(sheet,kAXParentAttribute,scene.open)
        expect(!(try scene.boundary()).isEmpty)
        scene.put(scene.open,"AXSheets",[] as CFArray)
        let modal=scene.node(kAXWindowRole,parent:scene.application)
        scene.put(modal,kAXModalAttribute,kCFBooleanTrue);scene.put(modal,"AXSheets",[] as CFArray)
        scene.declared.append(modal);scene.focused=modal
        expect(!(try scene.boundary()).isEmpty)
        scene.declared.removeLast();scene.focused=scene.open
        scene.nativeIdentity=false
        do { _=try scene.boundary();preconditionFailure("changed native identity authorized") }
        catch let e as DriverError { expect(e.code == .staleTarget) }
    }

    @MainActor static func requiredSurfaceAndOwnership() throws {
        let scene = Scene(); scene.bind()
        // A deep original surface omitted from declarations is rediscovered by
        // the same collector's bounded expansion, never by retention alone.
        scene.declared = [scene.other]; scene.focused = scene.other
        let anchor = scene.node("AXGroup", parent: scene.other)
        scene.children[CFHash(scene.other)] = [anchor]
        scene.children[CFHash(anchor)] = [scene.open]
        scene.put(scene.open,kAXParentAttribute,anchor)
        let expanded = try scene.access.inputBoundary(scene.app,nativeWindows:scene.windows(),contentRoot:scene.open,required:[scene.open])
        expect(expanded.expandedRequiredSurface && expanded.windows.contains { CFEqual($0.element,scene.open) })

        let member = Scene(); member.bind()
        let sheet = member.node(kAXSheetRole,parent:member.open)
        member.put(sheet,kAXModalAttribute,kCFBooleanTrue);member.put(sheet,"AXSheets",[] as CFArray)
        member.put(member.open,"AXSheets",[sheet] as CFArray)
        let graph = try member.access.inputBoundary(member.app,nativeWindows:member.windows(),contentRoot:member.open,required:[member.open,sheet])
        let ref = graph.windows.first { CFEqual($0.element,sheet) }!.ref
        let scope = member.access.observationScope(member.app,window:sheet,reference:ref)
        member.directory.remember(.init(owner:member.app,window:ref,scope:scope,parent:member.rootRef,element:sheet,native:nil))
        expect(try member.directory.inputWindowScope(instance:member.instance,root:member.rootRef,surface:scope).blocked.isEmpty)
        // Original observed parent metadata remains unchanged; current parent
        // is authoritative and must reject the cross-document retarget.
        member.put(member.open,"AXSheets",[] as CFArray)
        member.put(sheet,kAXParentAttribute,member.other);member.declared.append(sheet)
        do { _=try member.directory.inputWindowScope(instance:member.instance,root:member.rootRef,surface:scope)
            preconditionFailure("changed actual owner authorized")
        } catch let error as DriverError { expect([.staleTarget,.inputUnavailable].contains(error.code)) }
        // The object and its old parent are retained, but no live path exposes
        // it now. A successful retained role cannot revive this AX-only child.
        member.declared.removeLast()
        do { _=try member.directory.inputWindowScope(instance:member.instance,root:member.rootRef,surface:scope)
            preconditionFailure("retained-only AX child revived")
        } catch let error as DriverError { expect(error.code == .inputUnavailable) }
    }
    @MainActor static func originalOwnerBoundaries() throws {
        // The popup/leaf graph stays non-modal and unchanged while its original
        // logical owner gains a sheet or app-modal. This is the owner-negative
        // contract required by addressed popup actions as well as window input.
        let owner = Scene()
        owner.put(owner.open,kAXModalAttribute,kCFBooleanFalse)
        owner.bind(root:owner.other,nativeID:207)
        expect(try owner.boundary().isEmpty)
        let sheet = owner.node(kAXSheetRole,parent:owner.other)
        owner.put(sheet,kAXModalAttribute,kCFBooleanTrue);owner.put(sheet,"AXSheets",[] as CFArray)
        owner.put(owner.other,"AXSheets",[sheet] as CFArray)
        expect(!(try owner.boundary()).isEmpty)
        owner.put(owner.other,"AXSheets",[] as CFArray)
        let modal = owner.node(kAXWindowRole,parent:owner.application)
        owner.put(modal,kAXModalAttribute,kCFBooleanTrue);owner.put(modal,"AXSheets",[] as CFArray)
        owner.declared.append(modal)
        expect(!(try owner.boundary()).isEmpty)
        owner.declared.removeLast()
        owner.nativeIdentity=false
        do { _=try owner.boundary();preconditionFailure("original owner changed identity") }
        catch let e as DriverError { expect(e.code == .staleTarget) }
    }

    @MainActor static func uncertainFacts() throws {
        let positive = Scene(); positive.bind()
        let modal = positive.node(kAXWindowRole,parent:positive.application)
        positive.attributes[CFHash(modal)]?.removeValue(forKey:kAXRoleAttribute)
        positive.put(modal,kAXModalAttribute,kCFBooleanTrue);positive.put(modal,"AXSheets",[] as CFArray)
        positive.declared.insert(modal,at:0);positive.focused=modal
        expect(!(try positive.boundary()).isEmpty)

        let sheetScene = Scene(); sheetScene.bind()
        let sheet = sheetScene.node("AXGroup",parent:sheetScene.open)
        sheetScene.put(sheet,"AXSheets",[] as CFArray)
        // The declaration is processed before its owner's AXSheets. That
        // positive relation must survive an absent/contradictory optional role.
        sheetScene.declared.insert(sheet,at:0)
        sheetScene.put(sheetScene.open,"AXSheets",[sheet] as CFArray)
        expect(!(try sheetScene.boundary()).isEmpty)
        sheetScene.attributes[CFHash(sheet)]?.removeValue(forKey:kAXRoleAttribute)
        expect(!(try sheetScene.boundary()).isEmpty)
        sheetScene.put(sheet,kAXModalAttribute,kCFBooleanFalse)
        expect(try sheetScene.boundary().isEmpty)

        let unknown = Scene(); unknown.bind()
        let ownerless = unknown.node(kAXSheetRole,parent:unknown.application)
        unknown.put(ownerless,"AXSheets",[] as CFArray);unknown.declared.append(ownerless)
        do { _=try unknown.boundary();preconditionFailure("unknown sheet owner admitted") }
        catch let e as DriverError { expect(e.code == .inputUnavailable) }
        unknown.put(ownerless,kAXModalAttribute,kCFBooleanFalse)
        expect(try unknown.boundary().isEmpty)

        let conflict = Scene(); conflict.bind()
        let shared = conflict.node(kAXSheetRole,parent:conflict.open)
        conflict.put(shared,kAXModalAttribute,kCFBooleanTrue);conflict.put(shared,"AXSheets",[] as CFArray)
        conflict.put(conflict.open,"AXSheets",[shared] as CFArray)
        conflict.put(conflict.other,"AXSheets",[shared] as CFArray)
        do { _=try conflict.boundary();preconditionFailure("conflicting sheet owners admitted") }
        catch let e as DriverError { expect(e.code == .inputUnavailable) }

        let ordinary = Scene(); ordinary.bind()
        // Unknown optional modality on an unrelated ordinary window, and a
        // large incomplete control/file relation, are not global blockers.
        ordinary.attributes[CFHash(ordinary.other)]?.removeValue(forKey:kAXModalAttribute)
        ordinary.children[CFHash(ordinary.other)] = (0..<600).map { _ in ordinary.node("AXGroup",parent:ordinary.other) }
        expect(try ordinary.boundary().isEmpty)
        expect(ordinary.deepCount==0)
    }

    @MainActor static func observedModalHints() throws {
        let scene = Scene(); scene.bind()
        let modal = scene.node(kAXSheetRole,parent:scene.open)
        scene.put(modal,kAXModalAttribute,kCFBooleanTrue);scene.put(modal,"AXSheets",[] as CFArray)
        try scene.rememberModal(modal)
        let anchor = scene.node("AXGroup",parent:scene.open)
        scene.children[CFHash(scene.open)] = [anchor]
        scene.children[CFHash(anchor)] = [modal]
        scene.put(modal,kAXParentAttribute,anchor)
        expect(!(try scene.boundary()).isEmpty)
        // Old Role/Modal/Parent still read successfully after closure. Only
        // the current complete forward relation establishes absence.
        scene.children[CFHash(anchor)] = []
        expect(try scene.boundary().isEmpty)
        scene.children[CFHash(anchor)] = [modal]
        scene.attributes[CFHash(modal)]?.removeValue(forKey:kAXModalAttribute)
        expect(!(try scene.boundary()).isEmpty) // live AXSheet is document-modal
        scene.put(modal,kAXRoleAttribute,kAXWindowRole as CFString)
        do { _=try scene.boundary();preconditionFailure("known modal's current modality unknown") }
        catch let e as DriverError { expect(e.code == .inputUnavailable) }
        scene.put(modal,kAXModalAttribute,kCFBooleanFalse)
        expect(try scene.boundary().isEmpty)
        scene.put(modal,kAXModalAttribute,kCFBooleanTrue)
        // An unread suffix cannot prove this exact modal absent. A positive
        // identity in a partial prefix still proves that individual edge.
        let controls = (0..<600).map { _ in scene.node("AXGroup",parent:anchor) }
        scene.children[CFHash(anchor)] = controls + [modal]
        do { _=try scene.boundary();preconditionFailure("partial relation treated as modal absence") }
        catch let e as DriverError { expect(e.code == .inputUnavailable) }
        scene.children[CFHash(anchor)] = [modal] + controls
        expect(!(try scene.boundary()).isEmpty)
        scene.children[CFHash(anchor)] = []
        scene.invalid.insert(CFHash(modal))
        expect(try scene.boundary().isEmpty)
        scene.invalid.remove(CFHash(modal))
        scene.put(modal,kAXParentAttribute,anchor)
        let nextAnchor = scene.node("AXGroup",parent:scene.other)
        scene.children[CFHash(scene.other)] = [nextAnchor]
        scene.children[CFHash(nextAnchor)] = [modal]
        scene.onCount = { node in
            if CFEqual(node,anchor) {
                scene.put(modal,kAXParentAttribute,nextAnchor)
                scene.onCount=nil
            }
        }
        do { _=try scene.boundary();preconditionFailure("reparent during absence query treated as closed") }
        catch let e as DriverError { expect(e.code == .inputUnavailable) }
        expect(try scene.boundary().isEmpty) // fresh proof: modal belongs to other document
        scene.trusted=false
        do { _=try scene.boundary();preconditionFailure("known modal evidence lost on unavailable AX") }
        catch let e as DriverError { expect(e.code == .inputUnavailable) }
        let visual = Scene(); visual.bind();visual.trusted=false
        expect(try visual.boundary().isEmpty) // existing native visual degradation without a known modal
    }

    @MainActor static func modalOwnerAncestry() throws {
        let scene = Scene(); scene.bind()
        let (modal,owner) = try scene.uncertainModalOwner()
        let rows = try scene.access.inputBoundary(scene.app,nativeWindows:scene.windows(),contentRoot:scene.open,
                                                   required:[scene.open])
        let m = rows.windows.first { CFEqual($0.element,modal) }!
        let u = rows.windows.first { CFEqual($0.element,owner) }!
        expect(m.parent == u.ref && !m.parentUnknown && u.parentUnknown)
        func refused(_ scene: Scene) throws {
            do { _=try scene.boundary();preconditionFailure("positive modal lost through uncertain owner ancestry") }
            catch let e as DriverError { expect(e.code == .inputUnavailable) }
        }
        try refused(scene)
        scene.put(owner,kAXParentAttribute,scene.application)
        expect(try scene.boundary().isEmpty) // fresh complete chain proves another document
        scene.put(owner,kAXParentAttribute,scene.open)
        expect(!(try scene.boundary()).isEmpty) // current chain reaches original R
        let outer = scene.node(kAXWindowRole,parent:nil)
        scene.put(outer,kAXModalAttribute,kCFBooleanFalse);scene.put(outer,"AXSheets",[] as CFArray)
        scene.declared.append(outer);scene.put(owner,kAXParentAttribute,outer)
        try refused(scene) // M and immediate U known; a farther ancestor is unknown
        scene.put(outer,kAXParentAttribute,owner)
        try refused(scene) // positive modal's owner chain is cyclic
        scene.put(owner,kAXParentAttribute,scene.application)
        scene.put(modal,kAXModalAttribute,kCFBooleanFalse)
        scene.attributes[CFHash(outer)]?.removeValue(forKey:kAXParentAttribute)
        expect(try scene.boundary().isEmpty) // unknown ordinary outer alone is no blocker

        let native = Scene(); native.bind()
        let (nativeModal,_) = try native.uncertainModalOwner()
        native.put(native.other,"AXSheets",[nativeModal] as CFArray)
        native.put(nativeModal,kAXParentAttribute,native.other)
        // Remove the temporary U's forward edge; only exact current Other is owner.
        let temporary = native.declared.removeLast()
        native.put(temporary,"AXSheets",[] as CFArray)
        native.nativeParents[207]=999 // Native parent present but unresolved
        try refused(native)

        let missing = Scene(); missing.bind()
        let (missingModal,_) = try missing.uncertainModalOwner()
        let previous = missing.declared.removeLast()
        missing.put(previous,"AXSheets",[] as CFArray)
        missing.put(missingModal,kAXRoleAttribute,kAXWindowRole as CFString)
        missing.put(missingModal,kAXParentAttribute,missing.application)
        missing.ids[CFHash(missingModal)]=501
        missing.nativeIDs += [501,701];missing.nativeParents[501]=701
        missing.declared.append(missingModal)
        let graph = try missing.access.inputBoundary(missing.app,nativeWindows:missing.windows(),contentRoot:missing.open,
                                                       required:[missing.open])
        let entry = graph.windows.first { CFEqual($0.element,missingModal) }!
        expect(!entry.parentUnknown && entry.parent != nil && !graph.windows.contains { $0.ref == entry.parent })
        try refused(missing) // intermediate native owner has no AX row; not proven unrelated

        let fallback = Scene(); fallback.bind()
        let (_,unknownOwner) = try fallback.uncertainModalOwner()
        fallback.declared=[fallback.other,unknownOwner];fallback.focused=fallback.other
        let anchor=fallback.node("AXGroup",parent:fallback.other)
        fallback.children[CFHash(fallback.other)]=[anchor]
        fallback.children[CFHash(anchor)]=[fallback.open]
        fallback.put(fallback.open,kAXParentAttribute,anchor)
        do {
            let blocked=try fallback.boundary()
            FileHandle.standardError.write(Data("required-root fallback counterexample: positive M -> unknown U; blocked=\(blocked)\n".utf8))
            preconditionFailure("required fallback lost typed owner uncertainty")
        } catch let e as DriverError { expect(e.code == .inputUnavailable) }
        let expanded=try fallback.access.inputBoundary(fallback.app,nativeWindows:fallback.windows(),contentRoot:fallback.open,
                                                          required:[fallback.open])
        expect(expanded.expandedRequiredSurface && expanded.windows.contains { CFEqual($0.element,fallback.open) })
        expect(expanded.windows.first { CFEqual($0.element,unknownOwner) }?.parentUnknown == true)

        let changed = Scene(); changed.bind()
        let (changedModal,lostOwner) = try changed.uncertainModalOwner()
        changed.declared.removeLast();changed.declared.append(changedModal)
        changed.put(changedModal,kAXRoleAttribute,kAXWindowRole as CFString)
        changed.pidLimit[CFHash(lostOwner)]=1
        let lost = try changed.access.inputBoundary(changed.app,nativeWindows:changed.windows(),contentRoot:changed.open,
                                                       required:[changed.open])
        expect(lost.windows.first { CFEqual($0.element,changedModal) }?.parentUnknown == true)
        expect(!lost.windows.contains { CFEqual($0.element,lostOwner) })
        changed.pidReads[CFHash(lostOwner)]=0
        try refused(changed) // current parent existed, but its identity failed before row registration
    }

    @MainActor static func cgModalDeepRegression() throws {
        let scene=Scene();scene.bind()
        let modal=scene.node(kAXWindowRole,parent:scene.open)
        scene.put(modal,kAXModalAttribute,kCFBooleanTrue);scene.put(modal,"AXSheets",[] as CFArray)
        scene.ids[CFHash(modal)]=501;scene.nativeIDs.append(501)
        let native=scene.windows().first { $0.mapping.id==501 }!
        let ref=scene.access.windowReference(native,element:modal)
        let surface=scene.access.observationScope(scene.app,window:modal,reference:ref)
        scene.directory.remember(.init(owner:scene.app,window:ref,scope:surface,parent:scene.rootRef,
                                      element:modal,native:native,queryHint:.observedModal))
        let anchor=scene.node("AXGroup",parent:scene.open)
        scene.children[CFHash(scene.open)]=[anchor];scene.children[CFHash(anchor)]=[modal]
        scene.put(modal,kAXParentAttribute,anchor)
        expect(!(try scene.boundary(legacy:true)).isEmpty)
        let blocked=try scene.boundary()
        FileHandle.standardError.write(Data("known CG-backed modal: native501 present, legacy blocked; current blocked=\(blocked)\n".utf8))
        expect(!blocked.isEmpty)
    }

    @MainActor static func cgModalNativeOtherDocumentRegression() throws {
        let scene=Scene();scene.bind()
        let modal=scene.node(kAXWindowRole,parent:scene.other)
        scene.put(modal,kAXModalAttribute,kCFBooleanTrue);scene.put(modal,"AXSheets",[] as CFArray)
        let retained=scene.rememberCGModal(modal)
        scene.nativeParents[501]=207
        scene.invalid.insert(CFHash(modal))
        let graph=try scene.access.inputBoundary(scene.app,nativeWindows:scene.windows(),contentRoot:scene.open,
                                                 required:[scene.open])
        let other=graph.windows.first { CFEqual($0.element,scene.other) }!
        expect(other.ref==scene.otherRef && other.nativeID==207 && other.parent==nil && !other.parentUnknown)
        let relations=scene.access.windowParents(graph.windows,native:scene.windows(),pid:scene.app.processIdentifier)
        expect(relations.parents[retained.ref]==scene.otherRef && !relations.unresolved.contains(retained.ref))
        let legacy=try scene.boundary(legacy:true)
        expect(legacy.isEmpty)
        do {
            let current=try scene.directory.inputWindowScope(instance:scene.instance,root:scene.rootRef,surface:scene.scope)
            FileHandle.standardError.write(Data("CG modal native501->207 independentRoot; oldAXinvalid; legacy=\(legacy), typed=\(current.blocked)\n".utf8))
            expect(current.blocked.isEmpty && CFEqual(current.surface.element!,scene.open))
        } catch {
            FileHandle.standardError.write(Data("CG modal native501->207 independentRoot; oldAXinvalid; legacy=\(legacy), typed threw=\(error)\n".utf8))
            preconditionFailure("fresh native other-document proof became a global modal veto")
        }
        func sample() -> (Scene,AXUIElement) {
            let s=Scene();s.bind()
            let m=s.node(kAXWindowRole,parent:s.other)
            s.put(m,kAXModalAttribute,kCFBooleanTrue);s.put(m,"AXSheets",[] as CFArray)
            _=s.rememberCGModal(m);s.nativeParents[501]=207;s.invalid.insert(CFHash(m))
            return(s,m)
        }
        func refused(_ s:Scene) throws {
            do { _=try s.boundary();preconditionFailure("unproven native owner excluded a known modal hint") }
            catch let error as DriverError { expect(error.code == .inputUnavailable) }
        }
        let (target,_)=sample();target.nativeParents[501]=380;try refused(target)
        let (ancestor,_)=sample();ancestor.put(ancestor.open,kAXParentAttribute,ancestor.other);try refused(ancestor)
        let (zero,_)=sample();zero.nativeParents[501]=0;try refused(zero)
        let (unsupported,_)=sample();unsupported.unknownNativeParents.insert(501);try refused(unsupported)
        let (missing,_)=sample();missing.nativeParents[501]=999;try refused(missing)
        let (cycle,_)=sample();cycle.nativeParents[207]=501;try refused(cycle)
        let (selfCycle,_)=sample();selfCycle.nativeParents[501]=501;try refused(selfCycle)
        let (unknown,_)=sample();unknown.attributes[CFHash(unknown.other)]?.removeValue(forKey:kAXParentAttribute);try refused(unknown)
        let (lostOwner,_)=sample();lostOwner.invalid.insert(CFHash(lostOwner.other));try refused(lostOwner)
        let (lostRoot,_)=sample();lostRoot.invalid.insert(CFHash(lostRoot.open));try refused(lostRoot)
        let (changedOwner,_)=sample()
        let process=changedOwner.windows()[0].process
        changedOwner.nativeProcesses[207] = .init(pid:process.pid,parent:process.parent,seconds:2,microseconds:0,executable:process.executable)
        try refused(changedOwner)
        let (withinWindow,sheet)=sample();withinWindow.put(sheet,kAXRoleAttribute,kAXSheetRole as CFString)
        withinWindow.nativeParents[501]=0;try refused(withinWindow)
        let (conflict,m)=sample();conflict.invalid.remove(CFHash(m));conflict.declared.append(m)
        conflict.put(m,kAXParentAttribute,conflict.open);conflict.attributes[CFHash(m)]?.removeValue(forKey:kAXModalAttribute)
        try refused(conflict)
        let (nested,_)=sample();nested.nativeIDs.append(701);nested.nativeParents[501]=701;nested.nativeParents[701]=207
        expect(try nested.boundary().isEmpty) // every native edge is positive; endpoint is a known AX root
        nested.nativeParents[701]=0;try refused(nested)
        nested.nativeParents[701]=999;try refused(nested)
    }

    @MainActor static func cgModalBoundaries() throws {
        func sample() -> (scene:Scene,modal:AXUIElement,anchor:AXUIElement,ref:String,scope:String,native:NativeWindows.Window) {
            let s=Scene();s.bind()
            let m=s.node(kAXWindowRole,parent:s.open)
            s.put(m,kAXModalAttribute,kCFBooleanTrue);s.put(m,"AXSheets",[] as CFArray)
            let b=s.rememberCGModal(m)
            let a=s.node("AXGroup",parent:s.open)
            s.children[CFHash(s.open)]=[a];s.children[CFHash(a)]=[m];s.put(m,kAXParentAttribute,a)
            return(s,m,a,b.ref,b.scope,b.native)
        }
        func refused(_ s:Scene) throws {
            do { _=try s.boundary();preconditionFailure("live/unknown CG modal retired from AX evidence alone") }
            catch let e as DriverError { expect(e.code == .inputUnavailable) }
        }
        let invalid=sample();invalid.scene.invalid.insert(CFHash(invalid.modal));try refused(invalid.scene)
        let detached=sample();detached.scene.children[CFHash(detached.anchor)]=[];try refused(detached.scene)
        let hidden=sample();hidden.scene.nativeIDs.removeAll { $0==501 }
        hidden.scene.nativePresence[501] = .present(hidden.scene.info(501))
        expect(!(try hidden.scene.boundary()).isEmpty) // ordered out, not ended
        let closed=sample();closed.scene.nativeIDs.removeAll { $0==501 };closed.scene.invalid.insert(CFHash(closed.modal))
        expect(try closed.scene.boundary().isEmpty) // successful complete native absence
        closed.scene.nativePresence[501] = .unavailable;try refused(closed.scene)
        closed.scene.nativePresence[501] = .present([kCGWindowNumber as String:501,kCGWindowOwnerPID as String:1234])
        expect(try closed.scene.boundary().isEmpty) // original native ownership ended

        let child=Scene();child.bind()
        let m=child.node(kAXWindowRole,parent:child.open)
        child.put(m,kAXModalAttribute,kCFBooleanTrue);child.put(m,"AXSheets",[] as CFArray)
        let original=NativeProcessIdentity(pid:900,parent:child.app.processIdentifier,seconds:1,microseconds:0,executable:"/member")
        _=child.rememberCGModal(m,process:original)
        child.nativeIDs.removeAll { $0==501 };child.invalid.insert(CFHash(m));child.nativePresence[501] = .unavailable
        child.currentProcesses[900] = .init(pid:900,parent:1,seconds:2,microseconds:0,executable:"/member")
        expect(try child.boundary().isEmpty) // old process incarnation ended
        child.currentProcesses[900]=original;child.unavailableProcesses.insert(900);try refused(child)
        child.goneProcesses.insert(900);expect(try child.boundary().isEmpty)

        let current=sample()
        current.scene.put(current.modal,kAXModalAttribute,kCFBooleanFalse)
        expect(try current.scene.boundary().isEmpty)
        current.scene.directory.remember(.init(owner:current.scene.app,window:current.ref,scope:current.scope,parent:current.scene.rootRef,
                                             element:nil,native:current.native))
        current.scene.put(current.modal,kAXModalAttribute,kCFBooleanTrue)
        expect(!(try current.scene.boundary()).isEmpty) // unknown observation didn't erase positive query identity

        let replaced=sample();replaced.scene.invalid.insert(CFHash(replaced.modal))
        let proxy=replaced.scene.node(kAXWindowRole,parent:replaced.scene.open)
        replaced.scene.put(proxy,kAXModalAttribute,kCFBooleanTrue);replaced.scene.put(proxy,"AXSheets",[] as CFArray)
        replaced.scene.ids[CFHash(proxy)]=501;replaced.scene.declared.append(proxy)
        expect(!(try replaced.scene.boundary()).isEmpty)
        replaced.scene.put(proxy,kAXModalAttribute,kCFBooleanFalse)
        let selected=try replaced.scene.directory.inputWindowScope(instance:replaced.scene.instance,root:replaced.scene.rootRef,surface:replaced.scene.scope)
        expect(selected.blocked.isEmpty && CFEqual(selected.surface.element!,replaced.scene.open))
        replaced.scene.attributes[CFHash(proxy)]?.removeValue(forKey:kAXModalAttribute);try refused(replaced.scene)
        replaced.scene.put(proxy,kAXModalAttribute,kCFBooleanFalse);replaced.scene.ids[CFHash(proxy)]=502;try refused(replaced.scene)
        replaced.scene.ids[CFHash(proxy)]=501
        let duplicate=replaced.scene.node(kAXWindowRole,parent:replaced.scene.open)
        replaced.scene.put(duplicate,kAXModalAttribute,kCFBooleanFalse);replaced.scene.put(duplicate,"AXSheets",[] as CFArray)
        replaced.scene.ids[CFHash(duplicate)]=501;replaced.scene.declared.append(duplicate);try refused(replaced.scene)

        let inner=sample();inner.scene.invalid.insert(CFHash(inner.modal))
        let wrapper=inner.scene.node(kAXWindowRole,parent:inner.scene.open)
        inner.scene.put(wrapper,"AXSheets",[] as CFArray)
        let sheet=inner.scene.node(kAXSheetRole,parent:wrapper)
        inner.scene.put(sheet,kAXModalAttribute,kCFBooleanFalse);inner.scene.put(sheet,"AXSheets",[] as CFArray)
        inner.scene.ids[CFHash(sheet)]=501;inner.scene.declared += [wrapper,sheet]
        try refused(inner.scene) // inner AX surface's backing ID is not the CG window's modal declaration

        let other=sample();other.scene.put(other.modal,kAXParentAttribute,other.scene.other)
        other.scene.children[CFHash(other.scene.other)]=[other.modal];other.scene.children[CFHash(other.anchor)]=[]
        expect(try other.scene.boundary().isEmpty) // complete current path proves other document

        let own=Scene();own.bind()
        own.directory.remember(.init(owner:own.app,window:own.rootRef,scope:own.scope,parent:nil,element:own.open,
                                    native:own.windows()[0],queryHint:.observedModal))
        own.trusted=false;expect(try own.boundary().isEmpty)
        own.trusted=true;own.invalid.insert(CFHash(own.open))
        expect(try own.boundary().isEmpty) // CG self remains operable without an AX proxy
        let newOwn=own.node(kAXWindowRole,parent:own.application)
        own.put(newOwn,kAXModalAttribute,kCFBooleanFalse);own.put(newOwn,"AXSheets",[] as CFArray)
        own.ids[CFHash(newOwn)]=380;own.declared.append(newOwn);own.focused=newOwn
        let unchanged=try own.directory.inputWindowScope(instance:own.instance,root:own.rootRef,surface:own.scope)
        expect(unchanged.blocked.isEmpty && CFEqual(unchanged.surface.element!,own.open))
        own.put(newOwn,kAXModalAttribute,kCFBooleanTrue)
        let ownModal=try own.directory.inputWindowScope(instance:own.instance,root:own.rootRef,surface:own.scope)
        expect(ownModal.blocked.isEmpty && CFEqual(ownModal.surface.element!,own.open))

        let ancestor=Scene();ancestor.bind()
        ancestor.directory.remember(.init(owner:ancestor.app,window:ancestor.rootRef,scope:ancestor.scope,parent:nil,
                                         element:ancestor.open,native:ancestor.windows()[0],queryHint:.observedModal))
        let target=ancestor.node(kAXWindowRole,parent:ancestor.open)
        ancestor.put(target,kAXModalAttribute,kCFBooleanTrue);ancestor.put(target,"AXSheets",[] as CFArray)
        let binding=ancestor.rememberCGModal(target);ancestor.declared.append(target)
        ancestor.attributes[CFHash(ancestor.open)]?.removeValue(forKey:kAXModalAttribute)
        let allowed=try ancestor.directory.inputWindowScope(instance:ancestor.instance,root:ancestor.rootRef,surface:binding.scope)
        expect(allowed.blocked.isEmpty && CFEqual(allowed.surface.element!,target))
        ancestor.put(target,kAXParentAttribute,ancestor.other)
        do { _=try ancestor.directory.inputWindowScope(instance:ancestor.instance,root:ancestor.rootRef,surface:binding.scope)
            preconditionFailure("old ancestor hint authorized changed target path")
        } catch let e as DriverError { expect(e.code == .staleTarget) }
    }

}
