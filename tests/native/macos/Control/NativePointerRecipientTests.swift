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
            query.actualPID = { node in
                if CFEqual(node, self.nativeWindow) { return self.host.pid }
                if CFEqual(node, self.element) { return self.actual }
                if CFEqual(node, self.parent) { return self.endpointPID }
                if CFEqual(node, self.other) { return self.endpointPID }
                return self.host.pid
            }
            query.belongs = { _, nativeWindow, mapping in
                self.membership && nativeWindow.map { CFEqual($0, self.nativeWindow) } != false
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

        func resolve(captured: Bool = false) throws -> NativePointerRecipient {
            try NativePointerRecipient.resolve(
                host, nativeWindow: nativeWindow, element: captured ? nil : element, at: point, using: queries())
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

    @MainActor static func main() throws {
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

        // Unknown remote evidence cannot disable an independent ordinary host
        // pointer route or turn a visual-only surface into a public AX gate.
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
            let local = try scene.resolve(captured: captured)
            precondition(
                !local.isRemote && local.eventWindow.id == scene.host.id && local.eventWindow.pid == scene.host.pid,
                "missing or ordinary endpoint evidence must retain host delivery")
        }

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
