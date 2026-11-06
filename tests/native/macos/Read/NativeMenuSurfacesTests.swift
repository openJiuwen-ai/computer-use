// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices

@main struct NativeMenuSurfacesTests {
    @MainActor final class Scene {
        let root = AXUIElementCreateApplication(17001)
        let item = AXUIElementCreateApplication(17002)
        let menu = AXUIElementCreateApplication(17003)
        let app = NSRunningApplication.current
        var roles = [CFHashCode: String]()
        var parents = [CFHashCode: AXUIElement]()
        var bounds = [CFHashCode: CGRect]()
        var ids = [CFHashCode: CGWindowID]()
        var hits = 0
        var hit: AXUIElement?
        let frame = CGRect(x: 100, y: 200, width: 150, height: 90)
        init() {
            roles[CFHash(item)] = kAXMenuItemRole
            roles[CFHash(menu)] = kAXMenuRole
            roles[CFHash(root)] = kAXApplicationRole
            parents[CFHash(item)] = menu
            parents[CFHash(menu)] = root
            bounds[CFHash(menu)] = frame
            hit = item
        }
        func query() -> NativeMenuSurfaces.Query {
            var query = NativeMenuSurfaces.Query()
            query.trusted = { true }
            query.application = { _ in self.root }
            query.configure = { _ in }
            query.menuBar = { _ in nil }
            query.role = { self.roles[CFHash($0)] }
            query.children = { _ in [] }
            query.parent = { self.parents[CFHash($0)] }
            query.nativeID = { self.ids[CFHash($0)] }
            query.frame = { node, _ in self.bounds[CFHash(node)] }
            query.hit = { root, point in
                precondition(CFEqual(root, self.root))
                precondition(point == CGPoint(x: self.frame.midX, y: self.frame.midY))
                self.hits += 1
                return self.hit
            }
            query.now = { 100 }
            return query
        }
        func window(_ id: CGWindowID = 42, popup: Bool = true) -> NativeWindows.Window {
            .init(application: app,
                  process: .init(pid: app.processIdentifier, parent: 1, seconds: 1, microseconds: 0, executable: "/fixture"),
                  mapping: .init(id: id, pid: app.processIdentifier, frame: frame, title: "", width: 150, height: 90),
                  onScreen: true, level: popup ? Int(CGWindowLevelForKey(.popUpMenuWindow)) : 0)
        }
    }
    @MainActor static func main() {
        let scene = Scene()
        let result = NativeMenuSurfaces.classify([scene.window()], query: scene.query())
        precondition(result[0].menu.map { CFEqual($0, scene.menu) } == true,
                     "a visible control menu with no AXMenuBar lost its exact popup association")
        precondition(scene.hits == 1, "reuse one native popup hit, not a document scan")
        let normal = Scene()
        precondition(NativeMenuSurfaces.classify([normal.window(popup: false)], query: normal.query())[0].menu == nil)
        precondition(normal.hits == 0, "normal windows must not pay for popup matching")
        let duplicate = Scene()
        precondition(NativeMenuSurfaces.classify([duplicate.window(), duplicate.window(43)], query: duplicate.query())
            .allSatisfy { $0.menu == nil }, "ambiguous physical popups must remain independent")
        precondition(duplicate.hits == 0)
        let unknown = Scene(); unknown.hit = nil
        precondition(NativeMenuSurfaces.classify([unknown.window()], query: unknown.query())[0].menu == nil)
        let mismatch = Scene(); mismatch.bounds[CFHash(mismatch.menu)] = CGRect(x: 101, y: 200, width: 300, height: 90)
        precondition(NativeMenuSurfaces.classify([mismatch.window()], query: mismatch.query())[0].menu == nil)
        let cycle = Scene(); cycle.parents[CFHash(cycle.item)] = cycle.item
        precondition(NativeMenuSurfaces.classify([cycle.window()], query: cycle.query())[0].menu == nil)
        let hidden = Scene(); hidden.bounds.removeValue(forKey: CFHash(hidden.menu))
        precondition(NativeMenuSurfaces.classify([hidden.window()], query: hidden.query())[0].menu == nil)
        let declared = Scene()
        var query = declared.query()
        query.menuBar = { _ in declared.root }
        query.children = { node in CFEqual(node, declared.root) ? [declared.menu] : [] }
        precondition(NativeMenuSurfaces.classify([declared.window()], query: query)[0].menu != nil)
        precondition(declared.hits == 0, "already classified menu-bar windows must reuse their sampled facts")
        let hosted = Scene()
        let document = AXUIElementCreateApplication(17004)
        hosted.roles[CFHash(document)] = kAXWindowRole
        hosted.parents[CFHash(hosted.menu)] = document
        hosted.ids[CFHash(document)] = 99
        let associated = NativeMenuSurfaces.classify([hosted.window(), hosted.window(99, popup: false)], query: hosted.query())
        precondition(associated[0].menuHost == 99 && associated[0].menuParent == nil && associated[0].parentID == nil,
                     "AX document host must not be confused with menu nesting or physical parentage")
        precondition(NativeMenuSurfaces.current([associated[0]]) == 42,
                     "a document host must not remove the root of the menu chain")
        print("NativeMenuSurfacesTests passed")
    }
}
