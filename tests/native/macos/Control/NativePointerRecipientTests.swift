// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices

@main enum NativePointerRecipientTests {
    /// Independent host, AX proxy and service identities. No application or
    /// window is created; every native read used by recipient resolution is injected.
    @MainActor final class Scene {
        let nativeWindow = AXUIElementCreateApplication(91130)
        let element = AXUIElementCreateApplication(91131)
        let parent = AXUIElementCreateApplication(91132)
        let other = AXUIElementCreateApplication(91133)
        let area = AXUIElementCreateApplication(91134)
        let host = WindowGeometry.WindowMapping(
            id: 2859, pid: 91070, frame: CGRect(x: 100, y: 80, width: 400, height: 300),
            title: "host", width: 400, height: 300)
        let point = CGPoint(x: 180, y: 140)
        var hit: AXUIElement?
        var systemHit: AXUIElement?
        var systemHitCount = 0
        var actual: pid_t? = 91413
        var membership = true
        var proxyWindow: CGWindowID? = 2999
        var endpointWindow: CGWindowID? = 2858
        var otherWindow: CGWindowID? = 2859
        var endpointNode: AXUIElement?
        var endpointPID: pid_t = 91414
        var category: RemoteApplicationKind = .prohibitedApplication
        var hostedWeb = false
        var processes = [pid_t: NativeProcessIdentity]()
        var windows = [CGWindowID: NativeInputWindowHit.PublicWindow]()

        init() {
            hit = element
            endpointNode = parent
            for pid: pid_t in [91070, 91413, 91414, 91415] { processes[pid] = identity(pid) }
            windows[host.id] = .init(id: host.id, pid: host.pid, frame: host.frame)
            windows[2858] = .init(id: 2858, pid: 91414, frame: CGRect(x: 20, y: 40, width: 400, height: 300))
            windows[2999] = .init(id: 2999, pid: 91413, frame: host.frame)
        }

        func identity(_ pid: pid_t, seconds: UInt64 = 1) -> NativeProcessIdentity {
            NativeProcessIdentity(pid: pid, parent: 1, seconds: seconds, microseconds: 0, executable: "/fixture/\(pid)")
        }

        func queries() -> NativePointerRecipient.Queries {
            var query = NativePointerRecipient.Queries()
            query.hit = { pid, point in
                precondition(pid == self.host.pid && point == self.point)
                return self.hit
            }
            query.systemHit = { point in
                precondition(point == self.point)
                self.systemHitCount += 1
                return self.systemHit
            }
            query.actualPID = { node in
                if CFEqual(node, self.nativeWindow) { return self.host.pid }
                if CFEqual(node, self.element) { return self.actual }
                if CFEqual(node, self.parent) { return self.endpointPID }
                if CFEqual(node, self.other) { return self.endpointPID }
                return self.host.pid
            }
            query.belongs = { node, nativeWindow, mapping in
                self.membership && !CFEqual(node, self.other)
                    && nativeWindow.map { CFEqual($0, self.nativeWindow) } != false
                    && mapping.id == self.host.id && mapping.pid == self.host.pid
            }
            query.process = { self.processes[$0] }
            query.windowID = { node in
                if CFEqual(node, self.element) { return self.proxyWindow }
                if CFEqual(node, self.parent) { return self.endpointWindow }
                if CFEqual(node, self.other) { return self.otherWindow }
                if CFEqual(node, self.area) { return self.host.id }
                return self.host.id
            }
            query.webArea = { node, mapping, actual in
                guard self.hostedWeb, CFEqual(node, self.element), mapping.id == self.host.id,
                    actual == self.actual
                else { return nil }
                return self.area
            }
            query.endpoint = { node in
                guard CFEqual(node, self.element) else { return nil }
                return self.endpointNode.map {
                    NativeEventEndpoint.Candidate(element: $0, pid: self.endpointPID, kind: self.category)
                }
            }
            query.mapping = { self.windows[$0] }
            return query
        }

        func resolve(captured: Bool = false, foreground: Bool = false, preparedHit: AXUIElement? = nil) throws
            -> NativePointerRecipient
        {
            let foregroundHit: NativeInputWindowHit? = foreground
                ? .init(windowServer: .window(host.id), accessibility: nil, accessibilityElement: preparedHit) : nil
            return try NativePointerRecipient.resolve(
                host, nativeWindow: nativeWindow, element: captured ? nil : element, at: point,
                foregroundHit: foregroundHit, using: queries())
        }
    }

    @MainActor static func expectError(_ code: FailureCode, _ operation: () throws -> Void) {
        do {
            try operation()
            preconditionFailure("expected \(code)")
        } catch let error as DriverError {
            precondition(error.code == code, "unexpected native failure: \(error)")
        } catch { preconditionFailure("unexpected failure: \(error)") }
    }

    static func preparedLocalPointReadiness() {
        // Actual inactive-window experiment: unchanged key window, prepared
        // fixed local destination, app AXActive=false, no global foreground.
        // Ordinary routes retain their existing preparation rule.
        func ready(_ requirement: NativePointerReadiness.Requirement,
                   focus: NativePointerReadiness.WindowFocus = .target,
                   prepared: Bool = true) -> Bool {
            NativePointerReadiness.ready(
                kind: .click, foreground: false, windowKind: .keyRequiring, focus: focus,
                backgroundPrepared: prepared, backgroundReportedActive: false, requirement: requirement)
        }
        precondition(!ready(.contentReceiver))
        precondition(!ready(.foregroundWindowPoint))
        precondition(ready(.preparedLocalPoint))
        precondition(!ready(.preparedLocalPoint, prepared: false))
        // The return's queued provider-deactivation callback can retire the
        // shared transport cache after this point receipt was consumed. The
        // fixed gesture keeps its own receipt; ordinary routes do not.
        var cached = true
        let requirement = NativePointerReadiness.Requirement.preparedLocalPoint
        precondition(NativePointerReadiness.prepared(requirement: requirement, cached: cached))
        cached = false
        let pointReceipt = NativePointerReadiness.prepared(requirement: requirement, cached: cached)
        precondition(pointReceipt && ready(requirement, prepared: pointReceipt))
        precondition(!NativePointerReadiness.prepared(requirement: .contentReceiver, cached: cached))
        precondition(!ready(requirement, focus: .otherWindow, prepared: pointReceipt),
                     "a live point receipt does not hide a real key-window change")
        for focus: NativePointerReadiness.WindowFocus in [.otherWindow, .absent, .unresolved, .failed, .notRequired] {
            precondition(!ready(.preparedLocalPoint, focus: focus),
                         "returned point never borrows another or unconfirmed window receiver")
        }
    }

    @MainActor static func preparedPointLifetime() throws {
        let local = Scene()
        local.actual = local.host.pid
        let recipient = try local.resolve(captured: true)
        precondition(recipient.canPrepareBackgroundPoint)
        func proof() -> NativePreparedPointerPoint {
            NativePreparedPointerPoint(
                window: local.host, point: local.point, recipient: recipient,
                process: local.processes[local.host.pid]!)
        }
        let once = proof()
        let consumed = try once.consume(window: local.host, point: local.point, currentProcess: { local.processes[$0] })
        precondition(consumed.eventWindow.id == local.host.id)
        expectError(.invalidRequest) {
            _ = try once.consume(window: local.host, point: local.point, currentProcess: { local.processes[$0] })
        }
        expectError(.staleTarget) {
            _ = try proof().consume(window: local.host, point: .zero, currentProcess: { local.processes[$0] })
        }
        expectError(.staleTarget) {
            _ = try proof().consume(window: local.host, point: local.point, currentProcess: { local.identity($0, seconds: 2) })
        }
        let unknown = Scene()
        unknown.hit = nil
        let unknownRecipient = try unknown.resolve(captured: true)
        precondition(!unknownRecipient.canPrepareBackgroundPoint,
                     "unknown AX content retains the previous foreground recipe")
        let service = Scene()
        let serviceRecipient = try service.resolve(captured: true)
        precondition(!serviceRecipient.canPrepareBackgroundPoint,
                     "host local activity cannot qualify a remote event service")
    }

    @MainActor static func coarseServicePoint() throws {
        let scene = Scene()
        func qualify(_ hit: AXUIElement) throws -> NativePointerRecipient? {
            try NativePointerRecipient.coarseServiceHit(
                hit, target: scene.element, nativeWindow: scene.nativeWindow,
                host: scene.host, point: scene.point, using: scene.queries())
        }
        let shifted = try qualify(scene.nativeWindow)
        precondition(shifted == nil, "a coarse hit does not prove a shifted service's coordinate space")
        scene.windows[2858] = .init(id: 2858, pid: scene.endpointPID, frame: scene.host.frame)
        let retained = try qualify(scene.nativeWindow)
        precondition(retained?.eventWindow.id == 2858)
        let unchanged = try qualify(scene.nativeWindow)
        precondition(retained?.matchesService(unchanged!) == true)
        let unrelated = try qualify(scene.other)
        precondition(unrelated == nil, "an unrelated coarse hit cannot borrow the retained service")
        scene.processes[scene.endpointPID] = scene.identity(scene.endpointPID, seconds: 2)
        let restarted = try qualify(scene.nativeWindow)
        precondition(retained?.matchesService(restarted!) == false)
        expectError(.staleTarget) { _ = try retained?.validate(host: scene.host, using: scene.queries()) }
        scene.membership = false
        let unowned = try qualify(scene.nativeWindow)
        precondition(unowned == nil, "the service must remain part of the original presenter")
    }

    @MainActor static func main() throws {
        preparedLocalPointReadiness()
        try preparedPointLifetime()
        try coarseServicePoint()
        let retained = Scene()
        // A pointer target is not the keyboard focus or a separately queried hit.
        retained.hit = retained.other
        let remote = try retained.resolve()
        precondition(remote.isRemote && remote.presenter == retained.host.pid)
        precondition(remote.eventWindow.pid == 91414 && remote.eventWindow.id == 2858,
                     "service events must not target the host or borrow the skipped AX proxy's window")
        precondition(remote.eventWindow.frame != retained.host.frame)
        precondition(
            BackgroundEvents.pointerLocation(retained.point, window: remote.eventWindow) == CGPoint(x: 160, y: 100),
            "service-local coordinates must use the event window's own mapping")
        let checked = try remote.validate(host: retained.host, using: retained.queries())
        precondition(checked.id == 2858 && checked.pid == 91414)

        let captured = Scene()
        let pointRecipient = try captured.resolve(captured: true)
        precondition(pointRecipient.isRemote && pointRecipient.eventWindow.id == 2858,
                     "a fresh capture-point AX hit must establish the same remote endpoint")
        captured.hit = captured.other
        _ = try pointRecipient.validate(host: captured.host, using: captured.queries())
        precondition(pointRecipient.eventWindow.id == 2858,
                     "a selected gesture keeps its recipient instead of following later focus/point reads")

        // Cold image clicks on NSOpenPanel return a host AXSheet from the app
        // root even after activation. Exact foreground preparation makes the
        // system-wide hit authoritative for the content point inside that sheet.
        let coarse = Scene()
        coarse.hit = coarse.nativeWindow
        coarse.systemHit = coarse.element
        let prepared = try coarse.resolve(captured: true, foreground: true)
        precondition(prepared.isRemote && prepared.eventWindow.pid == coarse.endpointPID)
        precondition(coarse.systemHitCount == 1)

        // When the public window fallback already read the hit, dispatch uses
        // that same object instead of repeating the system-wide AX query.
        let shared = Scene()
        shared.hit = shared.nativeWindow
        let sharedRecipient = try shared.resolve(captured: true, foreground: true, preparedHit: shared.element)
        precondition(sharedRecipient.isRemote && shared.systemHitCount == 0)

        let covered = Scene()
        covered.hit = covered.nativeWindow
        covered.systemHit = covered.other
        let background = try covered.resolve(captured: true)
        precondition(!background.isRemote && covered.systemHitCount == 0,
                     "a background point must not query a covering application's AX content")
        let unrelated = try covered.resolve(captured: true, foreground: true)
        precondition(!unrelated.isRemote && covered.systemHitCount == 1,
                     "a system hit outside the real original parent chain cannot replace its receiver")

        let missingSystem = Scene()
        let knownRemote = try missingSystem.resolve(captured: true, foreground: true)
        precondition(knownRemote.isRemote && missingSystem.systemHitCount == 0,
                     "known remote content needs no additional system AX query")

        let coarseSystem = Scene()
        coarseSystem.systemHit = coarseSystem.nativeWindow
        let preciseApp = try coarseSystem.resolve(captured: true, foreground: true)
        precondition(preciseApp.isRemote && coarseSystem.systemHitCount == 0,
                     "a coarse system hit must not replace precise app content with its host")

        let coarsePrepared = Scene()
        let preciseAfterPreparation = try coarsePrepared.resolve(
            captured: true, foreground: true, preparedHit: coarsePrepared.nativeWindow)
        precondition(preciseAfterPreparation.isRemote && coarsePrepared.systemHitCount == 0,
                     "a cached coarse hit must not bypass the application's precise remote receiver")

        let visualOnly = Scene()
        visualOnly.hit = nil
        let visualRecipient = try visualOnly.resolve(captured: true, foreground: true)
        precondition(!visualRecipient.isRemote && visualOnly.systemHitCount == 1,
                     "AX absence is not a new public prerequisite on admitted visual window transport")

        // Missing AX is not a public gate. Once foreign content is positively
        // known, however, unresolved event addressing must not silently send
        // the gesture to the host and report an ordinary dispatch.
        for missing in 0..<12 {
            let scene = Scene()
            var captured = false
            switch missing {
            case 0: scene.hit = nil; captured = true
            case 1: scene.actual = nil
            case 2: scene.actual = scene.host.pid
            case 3: scene.endpointNode = nil
            case 4: scene.endpointWindow = nil
            case 5: scene.windows[2858] = nil
            case 6: scene.processes[91414] = nil
            case 7: scene.membership = false
            case 8: scene.category = .ordinaryApplication
            case 9: scene.category = .unknown
            case 10: scene.processes[91413] = nil
            default: scene.processes[scene.host.pid] = nil
            }
            if missing < 3 {
                let local = try scene.resolve(captured: captured)
                precondition(!local.isRemote && local.eventWindow.id == scene.host.id)
            } else {
                expectError(.inputUnavailable) { _ = try scene.resolve(captured: captured) }
            }
        }

        let hostAncestor = Scene()
        hostAncestor.endpointPID = hostAncestor.host.pid
        hostAncestor.category = .ordinaryApplication
        expectError(.inputUnavailable) { _ = try hostAncestor.resolve() }

        let contradictory = Scene()
        contradictory.windows[2858] = .init(id: 2858, pid: 91415, frame: contradictory.host.frame)
        expectError(.inputUnavailable) { _ = try contradictory.resolve() }

        // A renderer can be activation-prohibited while its input still belongs
        // to the proven hosting WebView. Process policy alone is not a service route.
        let web = Scene()
        web.hostedWeb = true
        web.proxyWindow = web.host.id
        web.endpointWindow = web.host.id
        let webHost = try web.resolve()
        precondition(
            !webHost.isRemote && webHost.eventWindow.id == web.host.id && webHost.eventWindow.pid == web.host.pid,
            "proven hosted Web content keeps the host's event forwarding route")
        web.hostedWeb = false
        expectError(.inputUnavailable) { _ = try web.resolve() }

        // Once a service is chosen, disappearance or contradiction is not an
        // instruction to silently retry the same gesture against the host.
        for mutation in 0..<13 {
            let scene = Scene()
            let original = try scene.resolve()
            switch mutation {
            case 0: scene.processes[scene.host.pid] = scene.identity(scene.host.pid, seconds: 2)
            case 1: scene.processes[91413] = nil
            case 2: scene.processes[91414] = scene.identity(91414, seconds: 2)
            case 3: scene.actual = 91415
            case 4: scene.endpointNode = scene.other
            case 5: scene.endpointPID = 91415
            case 6: scene.endpointNode = nil
            case 7: scene.category = .ordinaryApplication
            case 8: scene.endpointWindow = 2999
            case 9: scene.endpointWindow = nil
            case 10: scene.windows[2858] = .init(id: 2858, pid: 91415, frame: scene.host.frame)
            case 11: scene.windows[2858] = nil
            default: scene.membership = false
            }
            expectError(.staleTarget) { _ = try original.validate(host: scene.host, using: scene.queries()) }
            precondition(original.isRemote && original.eventWindow.pid == 91414 && original.eventWindow.id == 2858)
        }
        let changedHost = Scene()
        let originalHost = try changedHost.resolve()
        let otherHost = WindowGeometry.WindowMapping(
            id: 9999, pid: changedHost.host.pid, frame: changedHost.host.frame, title: nil, width: 400, height: 300)
        expectError(.staleTarget) { _ = try originalHost.validate(host: otherHost, using: changedHost.queries()) }

        // Translation of the same live event window is normal geometry evolution.
        // Validation returns its fresh mapping without changing the selected PID/ID.
        let translated = Scene()
        let moving = try translated.resolve()
        let currentFrame = CGRect(x: 40, y: 60, width: 400, height: 300)
        translated.windows[2858] = .init(id: 2858, pid: 91414, frame: currentFrame)
        let current = try moving.validate(host: translated.host, using: translated.queries())
        precondition(
            current.id == moving.eventWindow.id && current.pid == moving.eventWindow.pid && current.frame == currentFrame)

        // A provider may rebuild an endpoint's AX proxy without changing the
        // service process or event window. The original target remains bound;
        // object equality of an intermediate proxy is not event addressing.
        let rebuilt = Scene()
        let retainedRecipient = try rebuilt.resolve()
        rebuilt.endpointNode = rebuilt.other
        rebuilt.otherWindow = rebuilt.endpointWindow
        let rebuiltMapping = try retainedRecipient.validate(host: rebuilt.host, using: rebuilt.queries())
        precondition(rebuiltMapping.pid == 91414 && rebuiltMapping.id == 2858,
                     "same-service/same-window endpoint proxy replacement must remain usable")

        // A down can close its control/panel. Paired up still belongs to the
        // captured recipient process, not the now-missing AX tree or host state.
        let cleanup = Scene()
        let held = try cleanup.resolve()
        cleanup.membership = false
        cleanup.endpointNode = nil
        cleanup.endpointWindow = nil
        cleanup.windows[2858] = nil
        cleanup.processes[cleanup.host.pid] = nil
        cleanup.processes[91413] = nil
        precondition(held.canRelease(using: cleanup.queries()), "cleanup must not require post-click UI or host state")
        cleanup.processes[91414] = cleanup.identity(91414, seconds: 2)
        precondition(!held.canRelease(using: cleanup.queries()), "never release into a replacement service process")
        cleanup.processes[91414] = nil
        precondition(!held.canRelease(using: cleanup.queries()))
        print("Native pointer recipient regressions passed")
    }
}
