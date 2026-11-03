// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices

@main enum NativeReferenceLifetimeTests {
    @MainActor static func main() throws {
        let registry = ObservedElements<Int, String, String>(namespace: "test", snapshotLimit: 2, textLimit: 16)
        let original = registry.observe(1, evidence: "same", value: "original")
        let snapshot = registry.bind(original, text: "one")!
        for _ in 0..<20_000 {
            precondition(registry.observe(1, evidence: "same", value: "original") == original)
            precondition(registry.bind(original, text: "one") == snapshot,
                         "identical repeated text captures do not accumulate aliases")
        }
        for index in 2...20_000 {
            _ = registry.observe(index, evidence: "same", value: "other")
        }
        precondition(registry.target(original) == "original",
                     "unrelated observations cannot retire a live action identity")
        _ = registry.bind(original, text: "two")
        _ = registry.bind(original, text: "three")
        precondition(registry.text(snapshot) == nil, "immutable historical text remains a bounded cache")
        precondition(registry.target(original) == "original", "text eviction does not revoke action identity")
        let final = registry.bind(original, text: "current")!
        registry.invalidate { $0 == "original" }
        precondition(registry.target(original) == nil && registry.target(final) == nil,
                     "confirmed retirement invalidates the target and all action aliases")
        precondition(registry.text(final)?.value == "current", "historical text is independent of object lifetime")
        let replacement = registry.observe(1, evidence: "same", value: "new")
        precondition(replacement != original && registry.target(original) == nil,
                     "a new lifetime never resurrects or retargets a retired reference")
        print("PASS live references survive observation/text cache pressure; genuine retirement remains final")
        try verifyDirectoryLifetime()
    }

    @MainActor static func verifyDirectoryLifetime() throws {
        let app = NSRunningApplication.current
        let process = NativeProcessIdentity(pid: app.processIdentifier, parent: 0,
            seconds: 1, microseconds: 0, executable: "/reference-probe")
        let native = NativeWindows.Window(application: app, process: process,
            mapping: .init(id: 280, pid: app.processIdentifier, frame: CGRect(x: 1, y: 1, width: 100, height: 100),
                title: "hidden", width: 100, height: 100), onScreen: false, parentID: 0)
        var presence: WindowServerInfo.Presence = .present([
            kCGWindowNumber as String: NSNumber(value: 280),
            kCGWindowOwnerPID as String: NSNumber(value: app.processIdentifier),
            kCGWindowIsOnscreen as String: false
        ])
        var query = NativeWindowQueryAPI(attribute: { _, _ in nil })
        query.trusted = { false }
        let access = NativeApplicationAccess(windowQuery: query)
        let state = NativeDesktopState(applications: access, appSelections: AppSelections(validateOwner: { _ in }))
        var services = NativeDirectory.WindowServices()
        services.locate = { _ in app }
        services.listIfAvailable = { _ in [] }
        services.trusted = { false }
        services.owner = { _ in }
        services.process = { _ in process }
        services.isGone = { _ in false }
        services.presence = { _ in presence }
        let directory = NativeDirectory(state: state, session: "test", windowServices: services)
        directory.remember(.init(owner: app, window: "original", scope: "original", parent: nil,
            element: nil, native: native))
        for index in 0..<1100 {
            directory.remember(.init(owner: app, window: "other\(index)", scope: "other\(index)", parent: nil,
                element: nil, native: nil))
        }
        precondition(directory.surface("original") != nil, "unrelated surface discovery cannot evict an identity")
        _ = try directory.collectSurfaces("instance:probe", profile: .visual)
        precondition(directory.surface("original") != nil, "hidden native window is live despite filtered-list absence")
        presence = .unavailable
        _ = try directory.collectSurfaces("instance:probe", profile: .visual)
        precondition(directory.surface("original") != nil, "unknown WindowServer presence is not confirmed closure")
        presence = .absent
        _ = try directory.collectSurfaces("instance:probe", profile: .visual)
        precondition(directory.surface("original") == nil, "positive native closure releases retained scope")
        precondition(directory.surface("other0") != nil, "unknown AX-only scope is not collateral damage")
        print("PASS surface identity retention and exact lifetime cleanup preserve hidden/unknown windows")
    }
}
