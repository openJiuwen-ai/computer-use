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
    @MainActor static func selectForegroundWindow(_ id: CGWindowID) throws {
        guard let setFront, let mainConnection, let windowOwner, let connectionPSN else {
            throw DriverError("native foreground window preparation is unavailable", code: .inputUnavailable)
        }
        var owner: UInt32 = 0, psn: UInt64 = 0
        guard windowOwner(mainConnection(), id, &owner) == 0, connectionPSN(owner, &psn) == 0 else {
            throw DriverError("foreground window owner is unavailable", code: .staleTarget)
        }
        let activity = NativeInputActivity.begin(
            device: .focus, transport: .windowServer, mechanism: "selectForegroundWindow", window: id,
            stage: .preparation)
        let status = setFront(&psn, id, 0x200)
        activity?.returned(status: Int(status))
        guard status == 0 else { throw DriverError("native foreground activation failed", code: .inputUnavailable) }
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
    typealias KeyboardRecipient = NativeKeyboardRecipient

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
    static func hostedWebArea(_ focus: AXUIElement, in window: WindowGeometry.WindowMapping, actual expectedPID: pid_t)
        -> AXUIElement?
    {
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
        try NativeKeyboardRecipient.resolve(window)
    }

    @MainActor static func postKey(_ event: CGEvent, to recipient: KeyboardRecipient) {
        setField!(event, 40, Int64(recipient.pid))
        setField!(event, 51, Int64(recipient.window ?? 0))
        let activity = NativeInputActivity.begin(
            device: .keyboard, transport: .process, mechanism: "CGEvent.postToPid", pid: recipient.pid,
            window: recipient.window, event: event, presenterPID: recipient.presenter)
        event.postToPid(recipient.pid)
        activity?.returned()
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

    @MainActor static func post(
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
        let activity = NativeInputActivity.begin(
            device: .mouse, transport: .process, mechanism: "CGEvent.postToPid", pid: window.pid, window: window.id,
            event: event)
        event.postToPid(window.pid)
        activity?.returned()
    }
}
