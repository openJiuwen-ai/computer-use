// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices

@main enum NativeKeyboardRecipientTests {
    @MainActor final class Scene {
        // The AX object reports this proxy PID, independently of its service
        // and the presenter's application PID. No desktop process is launched.
        let focus = AXUIElementCreateApplication(91120)
        let other = AXUIElementCreateApplication(91121)
        let parent = AXUIElementCreateApplication(91122)
        let area = AXUIElementCreateApplication(91123)
        let window = WindowGeometry.WindowMapping(
            id: 2859, pid: 91070, frame: CGRect(x: 0, y: 0, width: 400, height: 300), title: nil,
            width: 400, height: 300)
        var current: AXUIElement?
        var actual: pid_t = 91413
        var belongs = true
        var serviceWindow: CGWindowID? = 2858
        var endpointWindow: CGWindowID? = 2858
        var category = RemoteApplicationKind.prohibitedApplication
        var endpointNode: AXUIElement?
        var endpointPID: pid_t = 91413
        var web = false
        var processes = [pid_t: NativeProcessIdentity]()

        init() {
            current = focus
            endpointNode = focus
            for pid: pid_t in [91070, 91413, 91414] {
                processes[pid] = identity(pid)
            }
        }

        func identity(_ pid: pid_t, seconds: UInt64 = 1) -> NativeProcessIdentity {
            NativeProcessIdentity(pid: pid, parent: 1, seconds: seconds, microseconds: 0, executable: "/fixture/\(pid)")
        }

        func queries() -> NativeKeyboardRecipient.Queries {
            var q = NativeKeyboardRecipient.Queries()
            q.focus = { _ in self.current }
            q.actualPID = { _ in self.actual }
            q.belongs = { _, window in self.belongs && window.id == self.window.id && window.pid == self.window.pid }
            q.process = { self.processes[$0] }
            q.windowID = { node in
                if CFEqual(node, self.focus) { return self.serviceWindow }
                if CFEqual(node, self.area) { return self.window.id }
                return self.endpointWindow
            }
            q.role = { _ in kAXTextFieldRole }
            q.endpoint = { _ in
                self.endpointNode.map {
                    NativeKeyboardEndpoint.Candidate(element: $0, pid: self.endpointPID, kind: self.category)
                }
            }
            q.webArea = { _, _, _ in self.web ? self.area : nil }
            return q
        }
    }

    @MainActor static func main() throws {
        let service = Scene()
        var reported: pid_t = 0
        precondition(AXUIElementGetPid(service.focus, &reported) == .success && reported == 91120)
        let recipient = try NativeKeyboardRecipient.resolve(service.window, using: service.queries())
        precondition(
            recipient.presenter == 91070 && recipient.pid == 91413 && recipient.window == 2858,
            "public AX PID equality must not reject a proven hosted service")
        try recipient.validateFocus(using: service.queries())

        let responder = Scene()
        responder.endpointNode = responder.parent
        responder.endpointPID = 91414
        responder.endpointWindow = nil
        let parent = try NativeKeyboardRecipient.resolve(responder.window, using: responder.queries())
        precondition(
            parent.pid == 91414 && parent.window == nil,
            "a parent endpoint must not borrow the skipped proxy's window ID")

        let ordinary = Scene()
        ordinary.category = .ordinaryApplication
        let host = try NativeKeyboardRecipient.resolve(ordinary.window, using: ordinary.queries())
        precondition(
            host.pid == 91070 && host.window == 2859,
            "ordinary embedded applications keep host event forwarding")

        let web = Scene()
        web.web = true
        web.serviceWindow = web.window.id
        web.endpointNode = nil
        let renderer = try NativeKeyboardRecipient.resolve(web.window, using: web.queries())
        precondition(
            renderer.pid == web.window.pid && renderer.window == web.window.id,
            "a proven hosted Web route does not require an AppKit renderer endpoint")

        for change in 0..<7 {
            let scene = Scene()
            let original = try NativeKeyboardRecipient.resolve(scene.window, using: scene.queries())
            switch change {
            case 0: scene.current = scene.other
            case 1: scene.belongs = false
            case 2: scene.processes[scene.window.pid] = scene.identity(scene.window.pid, seconds: 2)
            case 3: scene.processes[scene.actual] = nil
            case 4: scene.actual = 91414
            case 5: scene.endpointNode = scene.parent
            default: scene.serviceWindow = 9999
            }
            do {
                try original.validateFocus(using: scene.queries())
                preconditionFailure("changed receiver/owner/lifetime/endpoint must stop remaining input")
            } catch let error as DriverError { precondition(error.code == .staleTarget) }
        }
        for unavailable in 0..<3 {
            let scene = Scene()
            switch unavailable {
            case 0: scene.belongs = false
            case 1: scene.endpointNode = nil
            default: scene.processes[scene.actual] = nil
            }
            do {
                _ = try NativeKeyboardRecipient.resolve(scene.window, using: scene.queries())
                preconditionFailure("an unavailable endpoint cannot admit input")
            } catch let error as DriverError { precondition(error.code == .inputUnavailable) }
        }
    }
}
