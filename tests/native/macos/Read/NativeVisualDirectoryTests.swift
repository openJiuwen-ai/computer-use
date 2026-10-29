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
        var ids = [CFHashCode: CGWindowID]()
        var focused: AXUIElement?
        var nativeAvailable = true
        var nativeIDs: [CGWindowID] = [380]
        var nextID: pid_t = 19100
        lazy var access = NativeApplicationAccess(windowQuery: api())
        lazy var state = NativeDesktopState(applications: access, appSelections: AppSelections(validateOwner: { _ in }))
        lazy var directory: NativeDirectory = {
            var services = NativeDirectory.WindowServices()
            services.locate = { _ in self.app }
            services.listIfAvailable = { _ in self.nativeAvailable ? self.windows() : nil }
            services.list = { _ in self.windows() }
            services.trusted = { self.trusted }
            services.activate = { _ in self.activation += 1; return .init(kind: .alreadyEnabled) }
            services.focusedWindow = { _ in self.focused }
            services.registerRoot = { _, _, _ in
                self.content.append("root body registration poison")
                throw DriverError("scripted content poison", code: .observationIncomplete)
            }
            services.owner = { _ in }
            services.process = { _ in self.process }
            services.info = { id in [kCGWindowOwnerPID as String: self.app.processIdentifier, kCGWindowNumber as String: id] }
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
                if key == kAXWindowsAttribute { return declared as CFArray }
                if key == kAXFocusedWindowAttribute || key == kAXMainWindowAttribute { return focused }
                if key == kAXFocusedUIElementAttribute { content.append("AXFocusedUIElement"); return cell }
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
            api.inputWindow = { _, _, _ in self.content.append("inputWindow focused-control policy"); return self.window }
            api.presentedWindow = { node, _, _ in node }
            api.nativeID = { self.ids[CFHash($0)] }
            api.hint = { node, _ in self.ids[CFHash(node)].map { .init(frame: CGRect(x: 10, y: 10, width: 500, height: 400), title: "", id: $0) } }
            api.relations.count = { node, _ in
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
                                  title: "Native file view", width: 500, height: 400), onScreen: true, parentID: 0) }
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
        try visualWindowFacts()
        try nativeOnlyRefreshRetainsBinding()
        try declaredControlRootDoesNotOpenItsContents()
        try legacyContentPositiveControl()
        print("NativeVisualDirectoryTests passed")
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
