// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices

/// One menu command and the exact ancestor path selected by Python. The
/// selected native action or mouse mechanism controls the tracking lifetime.
@MainActor struct NativeMenuUnit {
    let application: NSRunningApplication
    let window: AXUIElement?
    let element: AXUIElement
    let root: AXUIElement
    let path: [AXUIElement]
    var attachedDialogs: NativeMenuAttachedDialogs? = nil

    private struct Branch {
        let opener: AXUIElement
        let menu: AXUIElement
    }
    private struct TrackedMenu {
        let menu: AXUIElement
        var windowID: CGWindowID?
    }
    private var background: BackgroundTarget { BackgroundTarget(application) }

    func receiverReady() throws -> Bool {
        try background.validate(input: true)
        if let window {
            return try confirmsReceiver(window, in: NativeMenuContext(application: application))
        }
        try background.validateWindowlessMenu()
        return true
    }

    private func confirmsReceiver(_ window: AXUIElement, in context: NativeMenuContext) throws -> Bool {
        if context.confirms(window) { return true }
        guard let attachedDialogs, let windows = context.attachedDialogWindows(window) else { return false }
        return try attachedDialogs.confirm(windows, application: context.applicationElement)
    }

    func readiness(enabled: Bool?) throws -> NativeReadiness {
        guard try receiverReady() else { return NativeReadiness([("receiver", "unknown")]) }
        return NativeReadiness(enabled == false ? [("enabled", "no")] : [])
    }

    static func needsDocumentPreparation(foreground: Bool, receiverReady: () throws -> Bool) rethrows -> Bool {
        guard foreground else { return true }
        return try !receiverReady()
    }

    private func validatePath() throws {
        guard path.last.map({ CFEqual($0, element) }) == true else {
            throw DriverError("the selected native menu path changed", code: .staleTarget)
        }
        switch try Self.membership(path, root: root) {
        case .confirmed: return
        case .changed: throw DriverError("the selected native menu path changed", code: .staleTarget)
        case .unavailable:
            throw DriverError("menu ancestry cannot be revalidated within its native budget", code: .observationIncomplete)
        }
    }

    static func membership(_ path: [AXUIElement], root: AXUIElement, reader: AXRelationReader = .init()) throws
        -> NativeContainment.Evidence
    {
        guard path.count <= 17 else { return .unavailable }
        var complete = true
        let matched = try ObservedPath.matches(path, root: root, same: { CFEqual($0, $1) }, children: { parent in
            if let children = try menuChildren(parent, limit: 512, reader: reader) { return children }
            complete = false
            return []
        })
        return matched ? .confirmed : complete ? .changed : .unavailable
    }

    static func menuChildren(_ element: AXUIElement, limit: Int, reader: AXRelationReader) throws -> [AXUIElement]? {
        try reader.completeArray(element, property: kAXChildrenAttribute, limit: limit, withinBudget: {
            try RequestDiagnostics.checkCancellation()
            return RequestDiagnostics.deadline.map { ProcessInfo.processInfo.systemUptime < $0 } ?? true
        }).value
    }

    private func declares(_ action: String, on element: AXUIElement) -> Bool {
        var raw: CFArray?
        return AXUIElementCopyActionNames(element, &raw) == .success && (raw as? [String])?.contains(action) == true
    }

    private func branches() throws -> [Branch] {
        try validatePath()
        var branches = [Branch]()
        guard path.count > 2 else { return branches }
        // The requested target is never a preparatory opener. For an explicit
        // openMenu request its own submenu must remain open after dispatch.
        for index in 1..<(path.count - 1)
        where AXAccess.read(path[index], kAXRoleAttribute).value as? String == kAXMenuRole {
            let opener = path[index - 1], menu = path[index]
            guard
                [kAXMenuBarItemRole, kAXMenuItemRole].contains(
                    AXAccess.read(opener, kAXRoleAttribute).value as? String ?? ""),
                declares(kAXPressAction, on: opener), declares(kAXCancelAction, on: menu)
            else {
                throw DriverError(
                    "the observed menu path has no paired native open/cancel preparation", code: .inputUnavailable)
            }
            branches.append(Branch(opener: opener, menu: menu))
        }
        return branches
    }

    /// Cleanup remains live after cancellation, just like paired mouse-up.
    /// Cancel only the retained native menus; never send a global Escape key.
    private func close(
        _ menus: [AXUIElement], process: NativeProcessIdentity, onPreparation: (String) -> Void,
        onCleanupUnknown: () -> Void, onNativePreparation: (NativePreparationEffect) -> Void,
        tracked: [TrackedMenu]? = nil
    ) async -> Bool {
        guard !menus.isEmpty else { return true }
        guard NativeProcessIdentity.read(application.processIdentifier) == process else {
            onCleanupUnknown()
            return false
        }
        var accepted = true
        for menu in menus.reversed() {
            onPreparation("close prepared menu")
            onNativePreparation(.init(kind: .menuContext, phase: .willSend, detail: "close prepared menu", window: nil))
            let status = NativeInputActivity.$stage.withValue(.cleanup) {
                AXMessaging.performAction(menu, kAXCancelAction as CFString)
            }
            if status != .success { accepted = false }
            onNativePreparation(.init(kind: .menuContext, phase: .returned, detail: "close prepared menu", window: nil))
        }
        if let tracked {
            accepted = await Self.waitForEvidence(seconds: 0.30, sample: {
                pendingTrackedMenus(tracked, pid: process.pid).isEmpty ? true : nil
            }, pause: Self.cleanupPause) != nil
        } else {
            await Self.cleanupPause(0.05)
            if menus.contains(where: { NativeMenuSurfaces.isPresentedPopup($0, pid: application.processIdentifier) }) {
                accepted = false
            }
        }
        if !accepted { onCleanupUnknown() }
        return accepted
    }

    private func withTracking(
        _ branches: [Branch], check: @MainActor () throws -> Void, onPreparation: (String) -> Void,
        onCleanupUnknown: () -> Void,
        onNativePreparation: (NativePreparationEffect) -> Void,
        pointerBody: ((WindowGeometry.WindowMapping) async throws -> Void)? = nil
    ) async throws {
        guard let process = NativeProcessIdentity.read(application.processIdentifier) else {
            throw DriverError("menu process disappeared", code: .staleTarget)
        }
        var opened = [TrackedMenu]()
        var pointerMenu: WindowGeometry.WindowMapping?
        let pointerActivity = pointerBody.map { _ in NativeForegroundResponsibility.desktopSnapshot().activity }
        func closePrepared(afterClick: Bool = false) async -> Bool {
            var remaining = opened
            if let pointerActivity {
                if afterClick, !opened.isEmpty {
                    // Give the native command its own menu-close transition
                    // before sending any cancellation of our preparation.
                    let closed = await Self.waitForEvidence(seconds: 0.25, sample: {
                        pendingTrackedMenus(opened, pid: process.pid).isEmpty ? true : nil
                    }, pause: Self.cleanupPause)
                    if closed != nil { return true }
                }
                remaining = pendingTrackedMenus(opened, pid: process.pid)
                if !remaining.isEmpty {
                    let current = NativeForegroundResponsibility.desktopSnapshot()
                    guard current.activity == pointerActivity, current.foregroundPID == process.pid else {
                        onCleanupUnknown()
                        return false
                    }
                }
            }
            return await close(
                remaining.map(\.menu), process: process, onPreparation: onPreparation, onCleanupUnknown: onCleanupUnknown,
                onNativePreparation: onNativePreparation, tracked: pointerBody == nil ? nil : remaining)
        }
        do {
            for branch in branches {
                try check()
                try validatePath()
                try await NativePhysicalInput.wait(.menuTracking, check: check)
                if pointerBody != nil, let mapping = NativeMenuSurfaces.mapping(branch.menu, pid: process.pid) {
                    pointerMenu = mapping
                    continue
                }
                guard try receiverReady(), AXAccess.read(branch.opener, kAXEnabledAttribute).value as? Bool != false,
                    declares(kAXPressAction, on: branch.opener), declares(kAXCancelAction, on: branch.menu)
                else {
                    throw DriverError(
                        "the selected menu branch cannot be prepared in its current context", code: .inputUnavailable)
                }
                // Retain cleanup before calling: an AX error can still leave
                // menu tracking active. Never invoke the requested leaf here.
                opened.append(.init(menu: branch.menu, windowID: nil))
                onPreparation("refresh selected menu branch")
                onNativePreparation(
                    .init(kind: .menuContext, phase: .willSend, detail: "refresh selected menu branch", window: nil))
                let status = AXMessaging.performAction(branch.opener, kAXPressAction as CFString)
                onNativePreparation(
                    .init(kind: .menuContext, phase: .returned, detail: "refresh selected menu branch", window: nil))
                guard status == .success else {
                    throw DriverError(
                        "menu opening could not be confirmed; requested command was not sent", code: .executionUnknown)
                }
                if pointerBody != nil {
                    guard let mapping = try await Self.waitForEvidence(seconds: 0.25, sample: {
                        try RequestDiagnostics.checkCancellation()
                        return NativeMenuSurfaces.mapping(branch.menu, pid: process.pid)
                    }, pause: { try await Task.sleep(for: .seconds($0)) }) else {
                        throw DriverError("the prepared menu did not publish its original popup", code: .inputUnavailable)
                    }
                    opened[opened.count - 1].windowID = mapping.id
                    pointerMenu = mapping
                } else {
                    try await Task.sleep(for: .milliseconds(50))
                }
            }
            if let pointerBody {
                guard let pointerMenu else {
                    throw DriverError("the original menu leaf has no tracked popup", code: .inputUnavailable)
                }
                try await pointerBody(pointerMenu)
            }
        } catch {
            _ = await closePrepared()
            throw error
        }
        guard await closePrepared(afterClick: pointerBody != nil) else {
            throw DriverError("menu preparation cleanup could not be confirmed", code: .executionUnknown)
        }
        if pointerBody == nil {
            try check()
            try validatePath()
        }
    }

    private func pendingTrackedMenus(_ menus: [TrackedMenu], pid: pid_t) -> [TrackedMenu] {
        guard !menus.isEmpty else { return [] }
        let windows = WindowServerInfo.onScreenWindows()
        return menus.filter { tracked in
            if let id = tracked.windowID, let windows { return windows.contains(id) }
            return NativeMenuSurfaces.visibleChildCount(tracked.menu, pid: pid) != 0
        }
    }

    static func waitForEvidence<Value>(
        seconds: TimeInterval, sample: () throws -> Value?,
        now: () -> TimeInterval = { ProcessInfo.processInfo.systemUptime }, pause: (TimeInterval) async throws -> Void
    ) async rethrows -> Value? {
        let deadline = now() + seconds
        while true {
            if let evidence = try sample() { return evidence }
            let remaining = deadline - now()
            guard remaining > 0 else { return nil }
            try await pause(min(0.015, remaining))
        }
    }

    private static func cleanupPause(_ seconds: TimeInterval) async {
        await withCheckedContinuation { continuation in
            DispatchQueue.main.asyncAfter(deadline: .now() + seconds) { continuation.resume() }
        }
    }

    static func requireTrackingPointer(_ pointer: CGPoint?, in leaf: CGRect) throws {
        guard let pointer else {
            throw DriverError("the physical pointer location is unavailable for native menu tracking", code: .inputUnavailable)
        }
        guard leaf.contains(pointer) else {
            throw DriverError(
                "native menu tracking uses the physical pointer, which is outside the selected item",
                code: .inputUnavailable,
                next: "use explicit desktop input when moving the physical pointer is acceptable")
        }
    }

    func prepare(
        foreground: Bool, check: @escaping @MainActor () throws -> Void,
        onPreparation: @escaping @MainActor (String) -> Void, onCleanupUnknown: @escaping @MainActor () -> Void,
        onNativePreparation deliveredPreparation: @escaping @MainActor (NativePreparationEffect) -> Void = { _ in },
        onFocusOutcome: @escaping @MainActor (NativeFocusOutcome) -> Void = { _ in },
        onPreparationWait: @escaping @MainActor (NativePreparationWait) -> Void = { _ in }, observeOutcome: Bool = false,
        refreshMenu: Bool = true
    ) async throws {
        func onNativePreparation(_ event: NativePreparationEffect) {
            if let scope = NativeForegroundRestorationScope.current { scope.record(event) }
            else if event.phase == .willSend {
                NativeForegroundResponsibility.current?.recordPreparation(target: background.capturedProcessIdentity)
            }
            deliveredPreparation(event)
        }
        try check()
        let semanticBranches = refreshMenu ? try branches() : nil
        if semanticBranches == nil { try validatePath() }
        guard let window else { return }
        if foreground {
            guard let id = BackgroundEvents.nativeWindowID(window) else {
                throw DriverError(
                    "menu context has no native window for foreground preparation", code: .inputUnavailable)
            }
            var changed = false
            try await ForegroundAssist().prepare(
                background, window: id,
                onAction: {
                    if !changed {
                        onNativePreparation(
                            .init(
                                kind: .foregroundActivation, phase: .willSend, detail: "foreground window preparation",
                                window: id))
                    }
                    changed = true
                    onPreparation("foreground window preparation")
                }, onWait: onPreparationWait, onCleanupUnknown: onCleanupUnknown)
            if changed {
                onNativePreparation(
                    .init(
                        kind: .foregroundActivation, phase: .returned, detail: "foreground window preparation",
                        window: id))
            }
        } else {
            let initial = NativeMenuContext(application: application)
            // Document selection is preparation, not another requirement for
            // an already confirmed foreground menu receiver.
            if try Self.needsDocumentPreparation(foreground: initial.foreground, receiverReady: {
                try confirmsReceiver(window, in: initial)
            }) {
                guard let document = initial.document(for: window) else {
                    throw NativePreparationUnavailable(
                        description: "the selected menu document context cannot be prepared in the background")
                }
                func set(_ object: AXUIElement, _ attribute: String, _ value: CFTypeRef) throws -> Bool {
                    guard AXAccess.writable(object, attribute).value == true else { return false }
                    try check()
                    onPreparation("menu context " + attribute)
                    onNativePreparation(
                        .init(kind: .windowSelection, phase: .willSend, detail: "menu context " + attribute, window: nil))
                    let status = AXMessaging.setAttributeValue(object, attribute as CFString, value)
                    onNativePreparation(
                        .init(kind: .windowSelection, phase: .returned, detail: "menu context " + attribute, window: nil))
                    if status == .invalidUIElement {
                        throw DriverError("menu context window expired", code: .staleTarget)
                    }
                    return true
                }
                if initial.main.map({ !CFEqual($0, document) }) ?? true {
                    _ = try set(document, kAXMainAttribute, kCFBooleanTrue)
                }
                if !(try receiverReady()), try !set(window, kAXFocusedAttribute, kCFBooleanTrue) {
                    _ = try set(
                        AXUIElementCreateApplication(application.processIdentifier), kAXFocusedWindowAttribute, window)
                }
            }
        }
        // A frontmost, confirmed recipient needs menu validation, not another
        // title click. NSMenu may keep a disabled value until tracking starts;
        // document titles are optional and may themselves be interactive.
        // In the background a disabled item can still indicate missing window
        // preparation, so retain that route before any authorized activation.
        if try !receiverReady()
            || (!background.isForeground && AXAccess.read(element, kAXEnabledAttribute).value as? Bool == false)
        {
            do {
                let mapping = try NativeWindowUnit(application: application, window: window).mapping()
                let outcome = try await NativeFocusPipeline.prepare(
                    background, window: mapping, nativeWindow: window, onPreparation: onPreparation,
                    onCleanupUnknown: onCleanupUnknown, beforeAction: check, onNativePreparation: onNativePreparation,
                    observeOutcome: observeOutcome)
                if observeOutcome { onFocusOutcome(outcome) }
            } catch let error as DriverError where error.code == .inputUnavailable {
                try check()
                if !foreground { throw NativePreparationUnavailable(description: error.description) }
                throw error
            }
        }
        for attempt in 0..<12 {
            try check()
            if try receiverReady() { break }
            if attempt < 11 { try await Task.sleep(for: .milliseconds(25)) }
        }
        guard try receiverReady() else {
            if foreground {
                throw DriverError("the selected menu recipient remains unconfirmed", code: .inputUnavailable)
            }
            throw NativePreparationUnavailable(
                description: "background menu context could not be established; no menu command was sent")
        }
        // NSMenu may retain both stale false AND stale true values after a
        // document switch. Validate its observed parent path on every command.
        guard let semanticBranches else { return }
        try await withTracking(
            semanticBranches, check: check, onPreparation: onPreparation, onCleanupUnknown: onCleanupUnknown,
            onNativePreparation: onNativePreparation)
        if AXAccess.read(element, kAXEnabledAttribute).value as? Bool == false {
            throw DriverError("the menu command remains disabled in the selected document", code: .inputUnavailable)
        }
    }

    func performPointer(
        _ request: NativePointerIntent, check: @escaping @MainActor () throws -> Void,
        guardOperation: @escaping @MainActor () throws -> Void,
        onDispatch: @escaping @MainActor () throws -> Void, onPosted: @escaping @MainActor () -> Void,
        onDelivered: @escaping @MainActor () -> Void,
        onPreparation: @escaping @MainActor (String) -> Void, onCleanupUnknown: @escaping @MainActor () -> Void,
        onNativePreparation: @escaping @MainActor (NativePreparationEffect) -> Void
    ) async throws {
        try check()
        let role = AXAccess.read(element, kAXRoleAttribute).value as? String
        guard request.kind == .click, request.button == "left", request.count == 1,
            background.isForeground, try receiverReady(), declares(kAXPressAction, on: element),
            NativeMenuCommandPolicy.requiresPointer(self, action: kAXPressAction, role: role)
        else {
            throw NativePreparationUnavailable(
                description: "The menu command's original addressed mouse receiver is unavailable.")
        }
        let branches = try branches()
        guard let menu = branches.last?.menu else {
            throw DriverError("the original menu leaf has no containing menu", code: .inputUnavailable)
        }
        try await NativeInputActivity.$stage.withValue(.preparation) {
            try await withTracking(
                branches, check: check, onPreparation: onPreparation, onCleanupUnknown: onCleanupUnknown,
                onNativePreparation: onNativePreparation
            ) { mapping in
                let pid = application.processIdentifier
                guard let frame = WindowGeometry.hint(element)?.frame, frame.width > 1, frame.height > 1
                else { throw DriverError("the menu leaf has no confirmed visible popup", code: .inputUnavailable) }
                let point = CGPoint(x: frame.midX, y: frame.midY)
                guard mapping.frame.contains(point) else {
                    throw DriverError("the menu leaf is outside its confirmed popup", code: .inputUnavailable)
                }
                // AppKit menu tracking still samples the physical pointer
                // when it consumes window-directed button events.
                try Self.requireTrackingPointer(CGEvent(source: nil)?.location, in: frame)
                let pointer = NativePointerUnit(
                    application: application, window: mapping, pointerRequirement: .foregroundWindowPoint,
                    recipientElement: element)
                let validate: @MainActor () throws -> Void = {
                    try check()
                    try validatePath()
                    guard background.isForeground, try receiverReady(),
                        AXAccess.read(element, kAXEnabledAttribute).value as? Bool != false,
                        NativeMenuSurfaces.matches(menu, window: mapping),
                        WindowGeometry.hint(element)?.frame == frame
                    else { throw DriverError("the menu leaf changed before mouse delivery", code: .inputUnavailable) }
                    let root = AXUIElementCreateApplication(pid)
                    AXUIElementSetMessagingTimeout(root, 0.05)
                    var hit: AXUIElement?
                    let status = AXUIElementCopyElementAtPosition(root, Float(point.x), Float(point.y), &hit)
                    guard status == .success, hit.map({ CFEqual($0, element) }) == true else {
                        throw DriverError("the mouse point does not identify the original menu leaf", code: .inputUnavailable)
                    }
                    try Self.requireTrackingPointer(CGEvent(source: nil)?.location, in: frame)
                }
                try await NativeInputActivity.$stage.withValue(.input) {
                    try await pointer.deliver(
                        request, from: point, to: nil, viewport: mapping.frame.size, check: validate,
                        guardOperation: guardOperation, onDispatch: onDispatch, onPosted: onPosted,
                        onCleanupUnknown: onCleanupUnknown, delivery: .windowDirected)
                }
                onDelivered()
            }
        }
    }

    func perform(
        action: String, opensMenu: Bool, check: @MainActor () throws -> Void, onDispatch: @MainActor () throws -> Void,
        onReturned: @MainActor () -> Void = {}
    ) async throws {
        try check()
        try validatePath()
        if action != kAXCancelAction {
            guard try receiverReady() else {
                throw DriverError("the menu recipient changed before dispatch", code: .inputUnavailable)
            }
            if AXAccess.read(element, kAXEnabledAttribute).value as? Bool == false {
                throw DriverError("the menu command is disabled in the selected document", code: .inputUnavailable)
            }
        }
        let isOpener =
            action == kAXPressAction
            && [kAXMenuBarItemRole, kAXMenuItemRole].contains(
                AXAccess.read(element, kAXRoleAttribute).value as? String ?? "")
            && AXAccess.elements(element, kAXChildrenAttribute, limit: 64)?.filter({
                AXAccess.read($0, kAXRoleAttribute).value as? String == kAXMenuRole
            }).count == 1
        if opensMenu, action == kAXPressAction, !isOpener {
            throw DriverError("the selected menu item no longer opens a submenu", code: .inputUnavailable)
        }
        if isOpener {
            try await NativePhysicalInput.wait(.menuTracking, check: check)
            // A requested submenu needs its live ancestor menus. Opening a
            // detached item can leave AppKit in an invisible tracking loop.
            // These opens are part of the requested effect, not temporary
            // validation. Cross the input barrier before the first one and
            // never close the resulting menu path on normal completion.
            for branch in try branches() {
                try check()
                try validatePath()
                try await NativePhysicalInput.wait(.menuTracking, check: check)
                guard try receiverReady(), AXAccess.read(branch.opener, kAXEnabledAttribute).value as? Bool != false,
                    declares(kAXPressAction, on: branch.opener)
                else { throw DriverError("the requested menu path changed while opening", code: .inputUnavailable) }
                try onDispatch()
                let status = AXMessaging.performAction(branch.opener, kAXPressAction as CFString)
                onReturned()
                guard status == .success else {
                    throw DriverError(
                        "menu path opening returned an AX error; effect unknown, not retried", code: .executionUnknown,
                        effect: .possible)
                }
                try await Task.sleep(for: .milliseconds(50))
            }
            try check()
            try validatePath()
            guard try receiverReady(), AXAccess.read(element, kAXEnabledAttribute).value as? Bool != false else {
                throw DriverError("the requested submenu recipient changed while opening", code: .inputUnavailable)
            }
        }
        try onDispatch()
        let status = AXMessaging.performAction(element, action as CFString)
        onReturned()
        guard status == .success else {
            throw DriverError(
                "menu action returned AX error \(status.rawValue); effect unknown, not retried",
                code: .executionUnknown, effect: .possible)
        }
    }
}
