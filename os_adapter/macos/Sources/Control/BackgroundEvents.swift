// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices

/// Window-scoped input. Optional SPI capabilities belong to their own routes;
/// a missing background transport does not disable ordinary foreground input.
@MainActor enum BackgroundEvents {
    private typealias SetLocation = @convention(c) (CGEvent, Double, Double) -> Void
    private typealias SetField = @convention(c) (CGEvent, UInt32, Int64) -> Void
    private typealias MainConnection = @convention(c) () -> UInt32
    private typealias WindowOwner = @convention(c) (UInt32, UInt32, UnsafeMutablePointer<UInt32>) -> Int32
    private typealias ConnectionPSN = @convention(c) (UInt32, UnsafeMutableRawPointer) -> Int32
    private static let library = dlopen(
        "/System/Library/PrivateFrameworks/SkyLight.framework/SkyLight", RTLD_LAZY | RTLD_LOCAL)
    private static func symbol<T>(_ name: String, as type: T.Type) -> T? {
        guard let library, let pointer = dlsym(library, name) else { return nil }
        return unsafeBitCast(pointer, to: type)
    }
    private static let setLocation = symbol("CGEventSetWindowLocation", as: SetLocation.self)
    private static let setField = symbol("SLEventSetIntegerValueField", as: SetField.self)
    private static let mainConnection = symbol("CGSMainConnectionID", as: MainConnection.self)
    private static let windowOwner = symbol("SLSGetWindowOwner", as: WindowOwner.self)
    private static let connectionPSN = symbol("SLSGetConnectionPSN", as: ConnectionPSN.self)

    // WindowServer's input hit respects pass-through/transparent surfaces;
    // front-to-back CGWindow rectangles alone do not. The independent public
    // fallback uses a system-wide AX hit, never a rectangle-based hit guess.
    private typealias FindWindow =
        @convention(c) (
            UInt32, UInt32, Int64, Int64, UnsafePointer<CGPoint>, UnsafeMutablePointer<CGPoint>,
            UnsafeMutablePointer<UInt32>, UnsafeMutablePointer<UInt32>
        ) -> Int32
    private static let findWindow = symbol("SLSFindWindowAndOwner", as: FindWindow.self)
    private static func windowServerHit(at point: CGPoint) -> NativeInputWindowHit.WindowServer {
        guard let mainConnection, let findWindow else { return .unavailable }
        var point = point, local = CGPoint.zero, window: UInt32 = 0, owner: UInt32 = 0
        let status = findWindow(mainConnection(), 0, 1, 0, &point, &local, &window, &owner)
        guard status == 0 else { return .failed(status) }
        guard window != 0, owner != 0 else { return .noWindow }
        return .window(window)
    }

    static func inputWindowHit(
        at point: CGPoint, retained: WindowGeometry.WindowMapping? = nil, nativeWindow: AXUIElement? = nil
    ) -> NativeInputWindowHit {
        NativeInputWindowHit.resolve(windowServerHit(at: point)) {
            NativeInputWindowQuery.accessibility(at: point, retained: retained, nativeWindow: nativeWindow)
        }
    }

    static func inputWindow(at point: CGPoint) -> CGWindowID? { inputWindowHit(at: point).windowID }

    private typealias SetFront = @convention(c) (UnsafeRawPointer, UInt32, UInt32) -> Int32
    private static let setFront = symbol("SLPSSetFrontProcessWithOptions", as: SetFront.self)

    static var capabilities: NativePointerCapabilities {
        .init(
            windowDirectedTransport: setLocation != nil && setField != nil,
            syntheticFocusNotifications: setField != nil, windowServerHit: mainConnection != nil && findWindow != nil,
            foregroundWindowPreparation: setFront != nil && mainConnection != nil && windowOwner != nil
                && connectionPSN != nil)
    }

    /// Request foreground selection of the exact window without a mouse primer.
    /// This changes real desktop focus and requires the caller's authorization.
    /// Returning does not prove native key-window state or input consumption.
    static func selectForegroundWindow(_ id: CGWindowID) throws {
        guard let setFront, let mainConnection, let windowOwner, let connectionPSN else {
            throw DriverError("native foreground window preparation is unavailable", code: .inputUnavailable)
        }
        var owner: UInt32 = 0, psn: UInt64 = 0
        guard windowOwner(mainConnection(), id, &owner) == 0, connectionPSN(owner, &psn) == 0 else {
            throw DriverError("foreground window owner is unavailable", code: .staleTarget)
        }
        guard setFront(&psn, id, 0x200) == 0 else {
            throw DriverError("native foreground activation failed", code: .inputUnavailable)
        }
    }

    private typealias ActualPID = @convention(c) (AXUIElement, UnsafeMutablePointer<pid_t>) -> Int32
    private typealias NativeWindow = @convention(c) (AXUIElement, UnsafeMutablePointer<CGWindowID>) -> Int32
    private static let accessibilityLibrary = dlopen(
        "/System/Library/Frameworks/ApplicationServices.framework/Frameworks/HIServices.framework/HIServices",
        RTLD_LAZY | RTLD_LOCAL)
    private static let actualPID: ActualPID? = accessibilityLibrary.flatMap { dlsym($0, "_AXUIElementGetActualPid") }
        .map { unsafeBitCast($0, to: ActualPID.self) }
    private static let nativeWindow: NativeWindow? = accessibilityLibrary.flatMap { dlsym($0, "_AXUIElementGetWindow") }
        .map { unsafeBitCast($0, to: NativeWindow.self) }

    /// An embedded view can expose AX in a different process from its keyboard
    /// receiver. Native view services receive their own events; WebView's host
    /// receives and forwards key events to its web renderer. Keep the event route
    /// and the AX service identity separate throughout validation.
    @MainActor struct KeyboardRecipient {
        let pid: pid_t
        let window: CGWindowID?
        let remoteElement: AXUIElement?
        let presenter: pid_t
        /// Identity of the AX service, which need not equal the event PID.
        let process: NativeProcessIdentity?
        let axWindow: CGWindowID?
        let hostRoute: HostRoute?

        func validate() throws {
            guard let remoteElement else { return }
            var actual: pid_t = 0, presented: pid_t = 0, native: CGWindowID = 0
            var role: CFTypeRef?
            guard let process, NativeProcessIdentity.read(process.pid) == process,
                AXUIElementGetPid(remoteElement, &presented) == .success, presented == presenter,
                actualPID?(remoteElement, &actual) == 0, actual == process.pid,
                (axWindow == nil || (nativeWindow?(remoteElement, &native) == 0 && native == axWindow)),
                AXUIElementCopyAttributeValue(remoteElement, kAXRoleAttribute as CFString, &role) == .success
            else {
                throw DriverError(
                    "embedded input recipient disappeared or changed; observe before continuing", code: .staleTarget)
            }
            if let hostRoute {
                guard NativeProcessIdentity.read(presenter) == hostRoute.hostProcess else {
                    throw DriverError(
                        "embedded input host disappeared or changed; remaining input was not dispatched",
                        code: .staleTarget)
                }
                switch hostRoute {
                case .hostedWeb(let hostedWeb):
                    guard
                        let currentArea = BackgroundEvents.hostedWebArea(
                            remoteElement, in: hostedWeb.window, actual: process.pid),
                        CFEqual(currentArea, hostedWeb.area),
                        KeyboardEventRoute.resolve(
                            hostWindow: hostedWeb.window.id,
                            focusWindow: BackgroundEvents.nativeWindowID(remoteElement),
                            webAreaWindow: BackgroundEvents.nativeWindowID(currentArea), hasHostedWebArea: true)
                            == .host
                    else {
                        throw DriverError(
                            "embedded web input route changed; remaining input was not dispatched", code: .staleTarget,
                            next: "read the input window before continuing")
                    }
                case .ordinaryApplication(let host):
                    guard RemoteApplicationKind.read(process.pid) == .ordinaryApplication,
                        let owner = WindowGeometry.owningWindow(remoteElement, pid: presenter, within: host.window),
                        WindowGeometry.matches(owner, host.window)
                    else {
                        throw DriverError(
                            "embedded application input route changed; remaining input was not dispatched",
                            code: .staleTarget, next: "read the input window before continuing")
                    }
                }
            }
        }

        func validateFocus() throws {
            let metric = RequestDiagnostics.metrics?.start(.recipientValidation)
            defer { metric?.end() }
            try validate()
            var current: CFTypeRef?
            if remoteElement == nil {
                // A confirmed control may create a new editor while typing.
                // That does not authorize continuing on a stale process route
                // when an embedded service takes over keyboard input.
                if AXUIElementCopyAttributeValue(
                    AXUIElementCreateApplication(presenter), kAXFocusedUIElementAttribute as CFString, &current)
                    == .success, let current, CFGetTypeID(current) == AXUIElementGetTypeID()
                {
                    var actual: pid_t = 0
                    guard actualPID?(unsafeDowncast(current, to: AXUIElement.self), &actual) == 0, actual == pid else {
                        throw DriverError(
                            """
                            keyboard recipient process changed or became unavailable; remaining input was \
                            not dispatched
                            """, code: .staleTarget, next: "read the input before continuing")
                    }
                }
                return
            }
            guard let remoteElement else { return }
            guard
                AXUIElementCopyAttributeValue(
                    AXUIElementCreateApplication(presenter), kAXFocusedUIElementAttribute as CFString, &current)
                    == .success, let current, CFEqual(current, remoteElement)
            else {
                throw DriverError(
                    "embedded input focus changed; remaining input was not dispatched. Observe before continuing",
                    code: .staleTarget)
            }
        }
    }

    /// A host event endpoint always retains an explicit proof and host lifetime;
    /// remote AX identity remains independently checked by KeyboardRecipient.
    @MainActor enum HostRoute {
        case hostedWeb(HostedWebFocus)
        case ordinaryApplication(OrdinaryApplicationFocus)

        var hostProcess: NativeProcessIdentity {
            switch self {
            case .hostedWeb(let proof): return proof.hostProcess
            case .ordinaryApplication(let proof): return proof.hostProcess
            }
        }
    }

    @MainActor struct OrdinaryApplicationFocus {
        let window: WindowGeometry.WindowMapping
        let hostProcess: NativeProcessIdentity
    }

    @MainActor struct HostedWebFocus {
        let area: AXUIElement
        let window: WindowGeometry.WindowMapping
        let hostProcess: NativeProcessIdentity
    }

    static func nativeWindowID(_ element: AXUIElement) -> CGWindowID? {
        var id: CGWindowID = 0
        return nativeWindow?(element, &id) == 0 && id != 0 ? id : nil
    }

    static func accessibilityProcessID(_ element: AXUIElement) -> pid_t? {
        var pid: pid_t = 0
        return actualPID?(element, &pid) == 0 && pid > 0 ? pid : nil
    }

    /// Only follow the focused object and its real parents. Merely finding a
    /// WebArea somewhere in an application's tree does not identify its editor.
    private static func hostedWebArea(
        _ focus: AXUIElement, in window: WindowGeometry.WindowMapping, actual expectedPID: pid_t
    ) -> AXUIElement? {
        var current: AXUIElement? = focus, seen = [AXUIElement]()
        let deadline = ProcessInfo.processInfo.systemUptime + 0.2
        while let element = current, seen.count < 16, ProcessInfo.processInfo.systemUptime < deadline {
            guard !seen.contains(where: { CFEqual($0, element) }) else { return nil }
            seen.append(element)
            var role: CFTypeRef?, presented: pid_t = 0
            guard AXUIElementGetPid(element, &presented) == .success, presented == window.pid,
                AXUIElementCopyAttributeValue(element, kAXRoleAttribute as CFString, &role) == .success
            else { return nil }
            if role as? String == "AXWebArea" {
                var actual: pid_t = 0
                guard actualPID?(element, &actual) == 0, actual == expectedPID,
                    let owner = WindowGeometry.owningWindow(element, pid: window.pid, within: window),
                    WindowGeometry.matches(owner, window)
                else { return nil }
                return element
            }
            if [kAXWindowRole, kAXSheetRole, kAXApplicationRole].contains(role as? String ?? "") { return nil }
            var parent: CFTypeRef?
            guard AXUIElementCopyAttributeValue(element, kAXParentAttribute as CFString, &parent) == .success,
                let parent, CFGetTypeID(parent) == AXUIElementGetTypeID()
            else { return nil }
            current = unsafeDowncast(parent, to: AXUIElement.self)
        }
        return nil
    }

    static func keyboardRecipient(_ window: WindowGeometry.WindowMapping) throws -> KeyboardRecipient {
        let local = KeyboardRecipient(
            pid: window.pid, window: window.id, remoteElement: nil, presenter: window.pid, process: nil, axWindow: nil,
            hostRoute: nil)
        guard let actualPID else {
            throw DriverError(
                "native keyboard recipient lookup is unavailable on this macOS version; requested input was not sent",
                code: .inputUnavailable)
        }
        let root = AXUIElementCreateApplication(window.pid)
        var raw: CFTypeRef?
        guard AXUIElementCopyAttributeValue(root, kAXFocusedUIElementAttribute as CFString, &raw) == .success, let raw,
            CFGetTypeID(raw) == AXUIElementGetTypeID()
        else { return local }
        let focus = unsafeDowncast(raw, to: AXUIElement.self)
        var pid: pid_t = 0, presented: pid_t = 0, id: CGWindowID = 0
        guard actualPID(focus, &pid) == 0, pid > 0 else {
            throw DriverError(
                "native keyboard recipient could not be resolved; requested input was not sent",
                code: .inputUnavailable, next: "read the input window again")
        }
        if pid == window.pid { return local }
        let host = WindowGeometry.owningWindow(focus, pid: window.pid, within: window)
        let publicStatus = AXUIElementGetPid(focus, &presented)
        let nativeStatus = nativeWindow?(focus, &id)
        guard publicStatus == .success, presented == window.pid, let host, WindowGeometry.matches(host, window) else {
            diagnose(
                """
                keyboard route mismatch: expected=\(window.pid)/\(window.id), presented=\(presented), \
                actual=\(pid)/\(id), host=\(String(describing: host.flatMap(WindowGeometry.hint)))
                """)
            throw DriverError(
                "embedded input recipient does not belong to the selected window; no requested input dispatched",
                code: .staleTarget)
        }
        let serviceWindow = nativeStatus == 0 && id != 0 ? id : nil
        if let area = hostedWebArea(focus, in: window, actual: pid),
            KeyboardEventRoute.resolve(
                hostWindow: window.id, focusWindow: serviceWindow, webAreaWindow: nativeWindowID(area),
                hasHostedWebArea: true) == .host
        {
            guard let hostProcess = NativeProcessIdentity.read(window.pid) else {
                throw DriverError(
                    "embedded web host is no longer available; requested input was not sent", code: .staleTarget)
            }
            let result = KeyboardRecipient(
                pid: window.pid, window: window.id, remoteElement: focus, presenter: window.pid,
                process: NativeProcessIdentity.read(pid), axWindow: serviceWindow,
                hostRoute: .hostedWeb(HostedWebFocus(area: area, window: window, hostProcess: hostProcess)))
            try result.validateFocus()
            diagnose("keyboard route: hosted web content, recipient=\(window.pid)/\(window.id), accessibility=\(pid)")
            return result
        }
        // A distinct AX process can be an ordinary application whose host
        // forwards events. Its independent AX window is not by itself proof
        // that AppKit key events should be addressed to that window/process.
        if RemoteApplicationKind.read(pid) == .ordinaryApplication {
            guard let hostProcess = NativeProcessIdentity.read(window.pid) else {
                throw DriverError(
                    "embedded application host is no longer available; requested input was not sent", code: .staleTarget
                )
            }
            let result = KeyboardRecipient(
                pid: window.pid, window: window.id, remoteElement: focus, presenter: window.pid,
                process: NativeProcessIdentity.read(pid), axWindow: serviceWindow,
                hostRoute: .ordinaryApplication(OrdinaryApplicationFocus(window: window, hostProcess: hostProcess)))
            try result.validateFocus()
            diagnose(
                """
                keyboard route: ordinary remote application, recipient=\(window.pid)/\(window.id), \
                accessibility=\(pid)
                """)
            return result
        }
        // Some embedded sheets expose no service WindowServer ID. Their host's
        // exact input window and focused AX object still identify the recipient;
        // address the service responder, never borrow the host's window number.
        let result = KeyboardRecipient(
            pid: pid, window: serviceWindow, remoteElement: focus, presenter: window.pid,
            process: NativeProcessIdentity.read(pid), axWindow: serviceWindow, hostRoute: nil)
        try result.validateFocus()
        diagnose(
            """
            keyboard route: native embedded view, presenter=\(window.pid)/\(window.id), \
            recipient=\(pid)/\(result.window.map(String.init) ?? "focused responder")
            """)
        return result
    }

    static func postKey(_ event: CGEvent, to recipient: KeyboardRecipient) {
        setField!(event, 40, Int64(recipient.pid))
        setField!(event, 51, Int64(recipient.window ?? 0))
        event.postToPid(recipient.pid)
    }

    static func requireKeyboardRoute() throws {
        guard setField != nil, actualPID != nil else {
            throw DriverError(
                "process-addressed keyboard input is unavailable on this macOS version", code: .inputUnavailable)
        }
    }

    /// Native transport availability and the exact WindowServer owner are the
    /// mouse-route contract. Keyboard receiver checks belong to keyboard units.
    static func requirePointerRoute(window: WindowGeometry.WindowMapping) throws {
        guard capabilities.windowDirectedTransport else {
            throw NativePreparationUnavailable(description: "window-directed event transport is unavailable")
        }
        try requireWindowOwner(window)
    }

    /// Focus notifications need integer-field addressing, not mouse location or
    /// physical-hit SPI. Their availability does not establish a key responder.
    static func requireSyntheticFocusRoute(window: WindowGeometry.WindowMapping) throws {
        guard capabilities.syntheticFocusNotifications else {
            throw NativePreparationUnavailable(description: "process-addressed focus notifications are unavailable")
        }
        try requireWindowOwner(window)
    }

    private static func requireWindowOwner(_ window: WindowGeometry.WindowMapping) throws {
        guard let info = WindowServerInfo.window(window.id),
            (info[kCGWindowOwnerPID as String] as? NSNumber)?.int32Value == window.pid
        else { throw DriverError("input window disappeared or changed owner", code: .staleTarget) }
    }

    static func keyboardPreparationIsCurrent(_ target: BackgroundTarget, window: WindowGeometry.WindowMapping) -> Bool {
        target.isForeground || SyntheticFocusCoordinator.shared.isCurrent(target, window: window, needsKeyWindow: true)
    }

    static func pointerPreparationIsCurrent(
        _ target: BackgroundTarget, window: WindowGeometry.WindowMapping, needsKeyWindow: Bool
    ) -> Bool {
        capabilities.windowDirectedTransport
            && SyntheticFocusCoordinator.shared.isCurrent(target, window: window, needsKeyWindow: needsKeyWindow)
    }

    static func post(
        _ event: CGEvent, window: WindowGeometry.WindowMapping, point: CGPoint? = nil, clickGroup: Int64 = 0
    ) {
        // prepare() resolves routing functions even when foreground targets skip activation.
        setField!(event, 40, Int64(window.pid))
        setField!(event, 51, Int64(window.id))  // NSEvent windowNumber
        if let point {
            setField!(event, 91, Int64(window.id))
            setField!(event, 92, Int64(window.id))
            setField!(event, 7, 3)  // window-routed mouse subtype
            setField!(event, 58, clickGroup)
            setLocation!(event, point.x - window.frame.minX, point.y - window.frame.minY)
        }
        // One delivery, never duplicate through a second route after an uncertain result.
        event.postToPid(window.pid)
    }
}
