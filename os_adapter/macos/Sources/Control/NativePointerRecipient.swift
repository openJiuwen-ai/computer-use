// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices

/// One gesture's event destination, separate from its displayed/semantic host.
/// Optional AX point evidence improves an already admitted window transport.
/// A known remote content boundary must resolve before dispatch. Once a service
/// is selected, new events validate it; a release never searches or retargets.
@MainActor struct NativePointerRecipient {
    typealias Endpoint = NativeEventEndpoint.Candidate<AXUIElement>
    @MainActor struct Queries {
        var hit: @MainActor (pid_t, CGPoint) -> AXUIElement? = { pid, point in
            NativeInputWindowQuery.pointHit(at: point, application: pid).element
        }
        var systemHit: @MainActor (CGPoint) -> AXUIElement? = { NativeInputWindowQuery.pointHit(at: $0).element }
        var actualPID: @MainActor (AXUIElement) -> pid_t? = BackgroundEvents.accessibilityProcessID
        var belongs: @MainActor (AXUIElement, AXUIElement?, WindowGeometry.WindowMapping) -> Bool = {
            NativePointerRecipient.belongs($0, to: $1, host: $2)
        }
        var process: @MainActor (pid_t) -> NativeProcessIdentity? = NativeProcessIdentity.read
        var windowID: @MainActor (AXUIElement) -> CGWindowID? = BackgroundEvents.nativeWindowID
        var endpoint: @MainActor (AXUIElement) throws -> Endpoint? = NativeEventEndpoint.read
        var webArea: @MainActor (AXUIElement, WindowGeometry.WindowMapping, pid_t) -> AXUIElement? = {
            BackgroundEvents.hostedWebArea($0, in: $1, actual: $2)
        }
        var mapping: @MainActor (CGWindowID) -> NativeInputWindowHit.PublicWindow? = {
            WindowServerInfo.window($0).flatMap(NativeInputWindowQuery.row)
        }
    }
    private struct Remote {
        let element: AXUIElement
        let nativeHost: AXUIElement?
        let host: WindowGeometry.WindowMapping
        let hostProcess: NativeProcessIdentity
        let elementProcess: NativeProcessIdentity
        let endpoint: Endpoint
        let process: NativeProcessIdentity
    }
    let presenter: pid_t
    let eventWindow: WindowGeometry.WindowMapping
    private let remote: Remote?
    var isRemote: Bool { remote != nil }

    func matchesService(_ other: Self) -> Bool {
        guard let remote, let candidate = other.remote else { return false }
        return presenter == other.presenter && eventWindow.id == other.eventWindow.id
            && remote.process == candidate.process && remote.elementProcess == candidate.elementProcess
            && CFEqual(remote.element, candidate.element)
    }

    private var localContentProcessConfirmed = false
    /// Address evidence only. The caller separately establishes actual local
    /// activity/key-window state after returning the user's foreground.
    var canPrepareBackgroundPoint: Bool { localContentProcessConfirmed }

    /// AX proxy public PIDs can differ along an authentic remote view chain.
    /// Prove the retained host through its real parents, not PID equality or
    /// keyboard focus. Do not search unrelated descendants to guess a receiver.
    private static func belongs(
        _ element: AXUIElement, to nativeHost: AXUIElement?, host: WindowGeometry.WindowMapping
    ) -> Bool {
        let deadline = ProcessInfo.processInfo.systemUptime + 0.2
        var current: AXUIElement? = element, seen = [AXUIElement]()
        while let node = current, seen.count < 32, ProcessInfo.processInfo.systemUptime < deadline {
            guard !seen.contains(where: { CFEqual($0, node) }) else { return false }
            if let nativeHost, CFEqual(node, nativeHost) { return true }
            if nativeHost == nil, BackgroundEvents.nativeWindowID(node) == host.id {
                var pid: pid_t = 0
                if AXUIElementGetPid(node, &pid) == .success, pid == host.pid { return true }
            }
            seen.append(node)
            current = AXAccess.element(node, kAXParentAttribute)
        }
        return false
    }

    static func resolve(
        _ host: WindowGeometry.WindowMapping, nativeWindow: AXUIElement?, element: AXUIElement?, at point: CGPoint,
        foregroundHit: NativeInputWindowHit? = nil
    ) throws -> Self {
        try RequestDiagnostics.checkCancellation()
        let result = try resolve(
            host, nativeWindow: nativeWindow, element: element, at: point, foregroundHit: foregroundHit, using: Queries())
        try RequestDiagnostics.checkCancellation()
        return result
    }

    /// A hit can stop at the presenting window or at a service ancestor.
    /// The caller proves the live parent path from the original target to the
    /// hit. Resolve the target itself so a coarse hit never becomes its endpoint.
    static func coarseServiceHit(
        _ hit: AXUIElement, target: AXUIElement, nativeWindow: AXUIElement,
        host: WindowGeometry.WindowMapping, point: CGPoint, using query: Queries = .init()
    ) throws -> Self? {
        guard let actual = query.actualPID(target), actual != host.pid else { return nil }
        try RequestDiagnostics.checkCancellation()
        do {
            let recipient = try resolve(host, nativeWindow: nativeWindow, element: target, at: point, using: query)
            guard recipient.isRemote, recipient.eventWindow.frame == host.frame,
                recipient.eventWindow.frame.contains(point) else { return nil }
            if CFEqual(hit, nativeWindow) {
                guard query.actualPID(hit) == host.pid, query.windowID(hit) == host.id else { return nil }
            } else {
                let ancestor = try resolve(host, nativeWindow: nativeWindow, element: hit, at: point, using: query)
                guard ancestor.isRemote, ancestor.eventWindow.id == recipient.eventWindow.id,
                    ancestor.remote?.process == recipient.remote?.process else { return nil }
            }
            try RequestDiagnostics.checkCancellation()
            return recipient
        } catch let error as DriverError where error.code == .inputUnavailable || error.code == .staleTarget {
            // This only withdraws the coarse geometry proof. An exact hit
            // or independent AX action can still qualify on its own evidence.
            try RequestDiagnostics.checkCancellation()
            return nil
        }
    }

    static func resolve(
        _ host: WindowGeometry.WindowMapping, nativeWindow: AXUIElement?, element: AXUIElement?, at point: CGPoint,
        foregroundHit: NativeInputWindowHit? = nil, using query: Queries
    ) throws -> Self {
        let local = Self(presenter: host.pid, eventWindow: host, remote: nil)
        // A retained element is the receiver evidence even when the provider's
        // point query is coarse. A coordinate gets a new content hit after the
        // original window has been prepared, not another coarse host-only hit.
        var target = element
        var source = element == nil ? "applicationPoint" : "retainedElement"
        if target == nil {
            let preparedHit = foregroundHit.flatMap { sampled -> AXUIElement? in
                guard sampled.windowID == host.id, let hit = sampled.accessibilityElement,
                    query.belongs(hit, nativeWindow, host) else { return nil }
                return hit
            }
            func hasForeignProcess(_ hit: AXUIElement?) -> Bool {
                hit.flatMap(query.actualPID).map { $0 != host.pid } ?? false
            }
            if hasForeignProcess(preparedHit) {
                target = preparedHit
                source = "preparedSystemWidePoint"
            } else {
                target = query.hit(host.pid, point)
                // A coarse host hit must not erase a more precise remote
                // receiver already found through the application. System AX
                // only fills a content-addressing gap in this exact foreground
                // window; it never searches another application's descendants.
                if !hasForeignProcess(target), let preparedHit {
                    target = preparedHit
                    source = "preparedSystemWidePoint"
                } else if !hasForeignProcess(target), let foregroundHit,
                    foregroundHit.windowID == host.id, foregroundHit.accessibilityElement == nil,
                    let hit = query.systemHit(point), query.belongs(hit, nativeWindow, host)
                {
                    target = hit
                    source = "systemWidePoint"
                }
            }
        }
        func hostRoute(_ reason: String) -> Self {
            diagnose([
                "kind": "pointer_recipient_resolution", "presenterPID": host.pid, "hostWindow": host.id,
                "recipientPID": host.pid, "eventWindow": host.id, "source": source, "resolution": reason
            ])
            var result = local
            result.localContentProcessConfirmed = reason == "hostPointProcess"
            return result
        }
        // No content AX is an unknown source, not a new gate on visual-only
        // window transport. This does not claim that the host has consumed it.
        guard let target else { return hostRoute("windowTransportWithoutContentHit") }
        guard let actual = query.actualPID(target) else { return hostRoute("windowTransportWithUnknownContentProcess") }
        guard actual != host.pid else { return hostRoute("hostPointProcess") }
        // A WebContent process may also be prohibited, while its host owns and
        // forwards NSEvents. Reuse the existing proven hosted-view boundary;
        // process activation policy alone cannot distinguish these routes.
        if let area = query.webArea(target, host, actual),
            KeyboardEventRoute.resolve(
                hostWindow: host.id, focusWindow: query.windowID(target),
                webAreaWindow: query.windowID(area), hasHostedWebArea: true) == .host
        { return hostRoute("hostedWebForwarding") }
        // A positive foreign content PID is not the same as absent AX. Silently
        // using the host here loses clicks in system-hosted panels. Failure is
        // local to this directed mechanism and happens before any pointer event.
        guard query.belongs(target, nativeWindow, host), let endpoint = try query.endpoint(target) else {
            throw DriverError("the remote pointer content has no resolved event receiver in its original window",
                              code: .inputUnavailable)
        }
        guard endpoint.kind == .prohibitedApplication, endpoint.pid != host.pid,
            let id = query.windowID(endpoint.element), let mapping = query.mapping(id),
            let hostProcess = query.process(host.pid), let elementProcess = query.process(actual),
            let process = query.process(endpoint.pid)
        else {
            throw DriverError("the remote pointer event receiver is not currently available", code: .inputUnavailable)
        }
        guard mapping.id == id, mapping.pid == endpoint.pid else {
            throw DriverError("the remote pointer endpoint's window has a different owner", code: .inputUnavailable)
        }
        let eventWindow = Self.mapping(mapping, imageBasis: host)
        let result = Self(
            presenter: host.pid, eventWindow: eventWindow,
            remote: Remote(
                element: target, nativeHost: nativeWindow, host: host, hostProcess: hostProcess,
                elementProcess: elementProcess, endpoint: endpoint, process: process))
        _ = try result.validate(host: host, using: query)
        diagnose([
            "kind": "pointer_recipient_resolution", "presenterPID": host.pid, "hostWindow": host.id,
            "accessibilityPID": actual, "endpointPID": endpoint.pid,
            "recipientPID": eventWindow.pid, "eventWindow": eventWindow.id,
            "source": source, "resolution": "remoteEndpoint",
            "hostFrame": [host.frame.minX, host.frame.minY, host.frame.width, host.frame.height],
            "eventFrame": [eventWindow.frame.minX, eventWindow.frame.minY, eventWindow.frame.width, eventWindow.frame.height]
        ])
        return result
    }

    private static func mapping(
        _ value: NativeInputWindowHit.PublicWindow, imageBasis host: WindowGeometry.WindowMapping
    ) -> WindowGeometry.WindowMapping {
        // Width/height describe the host's captured pixels; mouse addressing
        // consumes only the recipient's native frame. Never rebind a capture.
        .init(id: value.id, pid: value.pid, frame: value.frame, title: nil, width: host.width, height: host.height)
    }

    func validate(host: WindowGeometry.WindowMapping) throws -> WindowGeometry.WindowMapping {
        try RequestDiagnostics.checkCancellation()
        let result = try validate(host: host, using: Queries())
        try RequestDiagnostics.checkCancellation()
        return result
    }

    func validate(host: WindowGeometry.WindowMapping, using query: Queries) throws -> WindowGeometry.WindowMapping {
        guard let remote else { return host }
        guard host.id == remote.host.id, host.pid == remote.host.pid,
            query.process(host.pid) == remote.hostProcess,
            query.process(remote.elementProcess.pid) == remote.elementProcess,
            query.actualPID(remote.element) == remote.elementProcess.pid,
            query.belongs(remote.element, remote.nativeHost, host),
            query.process(remote.process.pid) == remote.process,
            let endpoint = try query.endpoint(remote.element), endpoint.pid == remote.endpoint.pid,
            endpoint.kind == remote.endpoint.kind,
            query.windowID(endpoint.element) == eventWindow.id,
            let current = query.mapping(eventWindow.id), current.id == eventWindow.id, current.pid == eventWindow.pid
        else {
            throw DriverError("the remote pointer recipient or its original window changed", code: .staleTarget)
        }
        // Same service window may move with the displayed host. Refresh only
        // its event-coordinate transform, never the selected receiver/target.
        return Self.mapping(current, imageBasis: host)
    }

    func canRelease() -> Bool { canRelease(using: Queries()) }
    func canRelease(using query: Queries) -> Bool {
        guard let remote else { return true }
        return query.process(remote.process.pid) == remote.process
    }

    /// A drag can move its receiving window. Cleanup may refresh only this
    /// captured destination's transform, without requiring a still-live control
    /// or host; closing the target is a normal consequence of mouse-down.
    func releaseWindow(host: WindowGeometry.WindowMapping) -> WindowGeometry.WindowMapping {
        guard remote != nil else { return host }
        let query = Queries()
        guard canRelease(using: query), let current = query.mapping(eventWindow.id),
            current.id == eventWindow.id, current.pid == eventWindow.pid
        else { return eventWindow }
        return Self.mapping(current, imageBasis: host)
    }
}
