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
        verifyEvidenceAndContentLifetimes()
        verifyTextByteBudget()
        try verifyDirectoryLifetime()
    }

    static func verifyEvidenceAndContentLifetimes() {
        let registry = ObservedElements<Int, String, String>(namespace: "identity", snapshotLimit: 4, textLimit: 32)
        let original = registry.observe(1, evidence: "original-object", value: "before-edit")
        let before = registry.bind(original, text: "old text")!
        let release = registry.retain(before)!
        precondition(registry.observe(1, evidence: "original-object", value: "after-edit") == original)
        let after = registry.bind(original, text: "new text")!
        precondition(registry.target(original) == "after-edit" && registry.target(before) == "after-edit",
                     "stable actions consume fresh metadata, independently of captured text")
        precondition(registry.text(before)?.value == "old text" && registry.text(after)?.value == "new text",
                     "captured text is immutable even when its original target is updated")

        let replacement = registry.observe(1, evidence: "replacement-object", value: "replacement")
        precondition(replacement != original && registry.target(original) == nil && registry.target(before) == nil,
                     "confirmed evidence conflict retires original actions even while a text capture is retained")
        precondition(registry.bind(original, text: "cannot revive") == nil && registry.retain(original) == nil)
        precondition(registry.text(before)?.value == "old text", "historical text does not become replacement content")
        release()
        release()
        registry.invalidate { $0 == "after-edit" }
        precondition(registry.target(replacement) == "replacement", "late old-object cleanup is local to that lifetime")
        print("PASS current action metadata, immutable text and actual replacement lifetimes remain distinct")
    }

    static func verifyTextByteBudget() {
        let registry = ObservedElements<Int, String, String>(namespace: "bytes", snapshotLimit: 8, textLimit: 6)
        let target = registry.observe(1, evidence: "same", value: "live")
        let first = registry.bind(target, text: "é")!
        let second = registry.bind(target, text: "中")!
        precondition(registry.text(first)?.value == "é" && registry.text(second)?.value == "中")
        let third = registry.bind(target, text: "🙂")!
        precondition(registry.text(first) == nil && registry.text(second) == nil,
                     "multibyte content is bounded by actual UTF-8 bytes, not character count")
        precondition(registry.text(third)?.value == "🙂" && registry.target(target) == "live")
        precondition(registry.bind(target, text: "1234567") == nil,
                     "oversized content does not publish an unavailable capture")
        precondition(registry.text(third)?.value == "🙂" && registry.target(target) == "live",
                     "rejecting oversized text must not revoke the live target or the last useful capture")
        let unavailable = registry.bind(target, text: nil)!
        precondition(registry.text(unavailable) != nil && registry.text(unavailable)?.value == nil,
                     "a captured unavailable Value differs from an expired capture")
        print("PASS bounded captured text preserves action identity and unavailable-value meaning")
    }

    @MainActor static func verifyDirectoryLifetime() throws {
        // A bare CLI can expose NSRunningApplication.current as a terminated
        // pid=-1 placeholder. The directory needs a real, live AppKit owner;
        // creating this prohibited application does not open or activate UI.
        NSApplication.shared.setActivationPolicy(.prohibited)
        let app = NSRunningApplication.current
        precondition(app.processIdentifier == getpid() && !app.isTerminated,
                     "directory fixture requires a live application owner")
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
        var currentProcess: NativeProcessIdentity? = process
        var processGone = false
        var catalogue: [NativeWindows.Window]? = []
        var query = NativeWindowQueryAPI(attribute: { _, _ in nil })
        query.trusted = { false }
        let access = NativeApplicationAccess(windowQuery: query)
        let state = NativeDesktopState(applications: access, appSelections: AppSelections(validateOwner: { _ in }))
        var services = NativeDirectory.WindowServices()
        services.locate = { _ in app }
        services.listIfAvailable = { _ in catalogue }
        services.trusted = { false }
        services.owner = { _ in }
        services.process = { _ in currentProcess }
        services.isGone = { _ in processGone }
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
        catalogue = nil
        currentProcess = nil
        _ = try directory.collectSurfaces("instance:probe", profile: .visual)
        precondition(directory.surface("original") != nil, "failed catalogue/process reads are not closure evidence")
        currentProcess = process
        presence = .present([kCGWindowNumber as String: NSNumber(value: 280)])
        _ = try directory.collectSurfaces("instance:probe", profile: .visual)
        precondition(directory.surface("original") != nil, "a partial WindowServer row cannot prove a changed owner")
        presence = .absent
        _ = try directory.collectSurfaces("instance:probe", profile: .visual)
        precondition(directory.surface("original") == nil, "positive native closure releases retained scope")
        precondition(directory.surface("other0") != nil, "unknown AX-only scope is not collateral damage")
        directory.remember(.init(owner: app, window: "reused-process", scope: "reused-process", parent: nil,
            element: nil, native: native))
        currentProcess = NativeProcessIdentity(pid: app.processIdentifier, parent: 0,
            seconds: 2, microseconds: 0, executable: "/reference-probe")
        presence = .unavailable
        _ = try directory.collectSurfaces("instance:probe", profile: .visual)
        precondition(directory.surface("reused-process") == nil,
                     "the same PID with a proven new process incarnation cannot retain an old native window")
        directory.remember(.init(owner: app, window: "ended-process", scope: "ended-process", parent: nil,
            element: nil, native: native))
        currentProcess = nil
        processGone = true
        _ = try directory.collectSurfaces("instance:probe", profile: .visual)
        precondition(directory.surface("ended-process") == nil,
                     "positive process exit retires its native window even without a readable catalogue")
        precondition(directory.surface("other0") != nil,
                     "native process evidence alone does not retire AX-only scopes of an independently live AppKit owner")
        print("PASS surface identity retention and exact lifetime cleanup preserve hidden/unknown windows")
    }
}
