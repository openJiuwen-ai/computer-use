// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices

/// A call-local event endpoint and its independent focused-object/window proof.
/// Every new event rechecks the retained proof; cleanup keeps the captured PID.
@MainActor struct NativeKeyboardRecipient {
    typealias Endpoint = NativeKeyboardEndpoint.Candidate<AXUIElement>
    /// Read-only OS leaves used by resolution and the final dispatch proof.
    @MainActor struct Queries {
        var focus: @MainActor (pid_t) -> AXUIElement? = {
            AXAccess.element(AXUIElementCreateApplication($0), kAXFocusedUIElementAttribute)
        }
        var actualPID: @MainActor (AXUIElement) -> pid_t? = BackgroundEvents.accessibilityProcessID
        var belongs: @MainActor (AXUIElement, WindowGeometry.WindowMapping) -> Bool = { element, window in
            WindowGeometry.owningWindow(element, pid: window.pid, within: window).map {
                WindowGeometry.matches($0, window)
            } == true
        }
        var process: @MainActor (pid_t) -> NativeProcessIdentity? = NativeProcessIdentity.read
        var windowID: @MainActor (AXUIElement) -> CGWindowID? = BackgroundEvents.nativeWindowID
        var role: @MainActor (AXUIElement) -> String? = { AXAccess.read($0, kAXRoleAttribute).value as? String }
        var endpoint: @MainActor (AXUIElement) throws -> Endpoint? = NativeKeyboardEndpoint.read
        var webArea: @MainActor (AXUIElement, WindowGeometry.WindowMapping, pid_t) -> AXUIElement? = {
            BackgroundEvents.hostedWebArea($0, in: $1, actual: $2)
        }
    }
    struct Scope {
        let window: WindowGeometry.WindowMapping
        let process: NativeProcessIdentity
    }

    enum RemoteRoute {
        case hostedWeb(AXUIElement, Scope)
        case ordinaryApplication(Scope)
        case viewService(Scope)

        var scope: Scope {
            switch self {
            case .hostedWeb(_, let scope), .ordinaryApplication(let scope), .viewService(let scope): return scope
            }
        }
    }

    let pid: pid_t
    let window: CGWindowID?
    let remoteElement: AXUIElement?
    let presenter: pid_t
    /// Actual owner of the focused AX object, independently of the event PID.
    let process: NativeProcessIdentity?
    let axWindow: CGWindowID?
    let remoteRoute: RemoteRoute?
    let endpoint: Endpoint?
    let endpointProcess: NativeProcessIdentity?

    static func resolve(_ window: WindowGeometry.WindowMapping) throws -> Self {
        try resolve(window, using: Queries())
    }

    static func resolve(_ window: WindowGeometry.WindowMapping, using query: Queries) throws -> Self {
        guard let focus = query.focus(window.pid) else {
            return local(window)
        }
        guard let actual = query.actualPID(focus) else {
            throw DriverError(
                "native keyboard recipient is unavailable; no requested input sent", code: .inputUnavailable)
        }
        if actual == window.pid { return local(window) }
        // AX proxies can advertise a service PID while their proven owning
        // window is the presenter's panel. PID equality is not containment.
        guard query.belongs(focus, window), let host = query.process(window.pid),
            let process = query.process(actual)
        else {
            throw DriverError(
                "the focused input has no confirmed event recipient in the selected window; no requested input sent",
                code: .inputUnavailable)
        }
        let scope = Scope(window: window, process: host)
        let serviceWindow = query.windowID(focus)
        let route: RemoteRoute
        let recipient: pid_t
        let eventWindow: CGWindowID?
        let endpoint: Endpoint?
        let endpointProcess: NativeProcessIdentity?
        if let area = query.webArea(focus, window, actual),
            KeyboardEventRoute.resolve(
                hostWindow: window.id, focusWindow: serviceWindow,
                webAreaWindow: query.windowID(area), hasHostedWebArea: true) == .host
        {
            route = .hostedWeb(area, scope)
            recipient = window.pid
            eventWindow = window.id
            endpoint = nil
            endpointProcess = nil
        } else {
            guard let resolved = try query.endpoint(focus),
                let process = query.process(resolved.pid)
            else {
                throw DriverError(
                    "native event endpoint is unavailable; no requested input sent", code: .inputUnavailable)
            }
            endpoint = resolved
            endpointProcess = process
            if resolved.kind == .ordinaryApplication {
                // Ordinary applications receive host-forwarded AppKit events;
                // their independent AX window is not an event destination proof.
                route = .ordinaryApplication(scope)
                recipient = window.pid
                eventWindow = window.id
            } else {
                route = .viewService(scope)
                recipient = resolved.pid
                // A skipped non-event process must not lend its window to the
                // service endpoint. An absent service ID uses its focused responder.
                eventWindow = query.windowID(resolved.element)
            }
        }
        let result = Self(
            pid: recipient, window: eventWindow, remoteElement: focus, presenter: window.pid,
            process: process, axWindow: serviceWindow, remoteRoute: route,
            endpoint: endpoint, endpointProcess: endpointProcess)
        try result.validateFocus(using: query)
        var reported: pid_t = 0
        let reportedStatus = WindowGeometry.inputDiagnosticsEnabled ? AXUIElementGetPid(focus, &reported) : .failure
        diagnose(
            [
                "kind": "keyboard_recipient_resolution", "presenterPID": window.pid,
                "reportedPID": reportedStatus == .success ? reported as Any : NSNull(),
                "accessibilityPID": actual, "endpointPID": endpoint?.pid as Any? ?? NSNull(),
                "recipientPID": recipient, "hostWindow": window.id,
                "eventWindow": eventWindow as Any? ?? NSNull(),
            ] as [String: Any])
        return result
    }

    private static func local(_ window: WindowGeometry.WindowMapping) -> Self {
        Self(
            pid: window.pid, window: window.id, remoteElement: nil, presenter: window.pid,
            process: nil, axWindow: nil, remoteRoute: nil, endpoint: nil, endpointProcess: nil)
    }

    func validate() throws {
        try validate(using: Queries())
    }

    func validate(using query: Queries) throws {
        guard let focus = remoteElement else { return }
        guard let process, query.process(process.pid) == process,
            query.actualPID(focus) == process.pid,
            axWindow == nil || query.windowID(focus) == axWindow,
            query.role(focus) != nil,
            let route = remoteRoute, query.process(presenter) == route.scope.process,
            query.belongs(focus, route.scope.window)
        else {
            throw DriverError(
                "embedded input recipient or window changed; remaining input was not sent", code: .staleTarget)
        }
        if case .hostedWeb(let area, let scope) = route {
            guard let currentArea = query.webArea(focus, scope.window, process.pid),
                CFEqual(currentArea, area),
                KeyboardEventRoute.resolve(
                    hostWindow: scope.window.id, focusWindow: query.windowID(focus),
                    webAreaWindow: query.windowID(currentArea), hasHostedWebArea: true) == .host
            else {
                throw DriverError("embedded web input route changed; remaining input was not sent", code: .staleTarget)
            }
        } else {
            guard let endpoint, let endpointProcess, query.process(endpointProcess.pid) == endpointProcess,
                let current = try query.endpoint(focus), current.pid == endpoint.pid,
                current.kind == endpoint.kind, CFEqual(current.element, endpoint.element)
            else {
                throw DriverError("native event endpoint changed; remaining input was not sent", code: .staleTarget)
            }
        }
    }

    func validateFocus() throws {
        try validateFocus(using: Queries())
    }

    func validateFocus(using query: Queries) throws {
        let metric = RequestDiagnostics.metrics?.start(.recipientValidation)
        defer { metric?.end() }
        try validate(using: query)
        let current = query.focus(presenter)
        if let remoteElement {
            guard let current, CFEqual(current, remoteElement) else {
                throw DriverError("embedded input focus changed; remaining input was not sent", code: .staleTarget)
            }
        } else if let current {
            guard query.actualPID(current) == pid else {
                throw DriverError(
                    "keyboard recipient process changed; remaining input was not sent", code: .staleTarget)
            }
        }
    }
}
