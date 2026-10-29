// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices

/// One menu command and the exact ancestor path selected by Python. Window
/// preparation and temporary menu tracking finish before the leaf is sent.
/// This unit never locates a replacement item or chooses a foreground fallback.
@MainActor struct NativeMenuUnit {
    let application: NSRunningApplication
    let window: AXUIElement?
    let element: AXUIElement
    let root: AXUIElement
    let path: [AXUIElement]

    private struct Branch {
        let opener: AXUIElement
        let menu: AXUIElement
    }
    private var background: BackgroundTarget { BackgroundTarget(application) }

    func receiverReady() throws -> Bool {
        try background.validate(input: true)
        if let window { return NativeMenuContext(application: application).confirms(window) }
        try background.validateWindowlessMenu()
        return true
    }

    func readiness(enabled: Bool?) throws -> NativeReadiness {
        guard try receiverReady() else { return NativeReadiness([("receiver", "unknown")]) }
        return NativeReadiness(enabled == false ? [("enabled", "no")] : [])
    }

    private func validatePath() throws {
        guard path.last.map({ CFEqual($0, element) }) == true, path.count <= 17,
            try ObservedPath.matches(
                path, root: root, same: { CFEqual($0, $1) },
                children: { parent in
                    guard let children = AXAccess.elements(parent, kAXChildrenAttribute, limit: 512) else {
                        throw DriverError(
                            "menu ancestry cannot be revalidated within its native budget", code: .observationIncomplete
                        )
                    }
                    return children
                })
        else { throw DriverError("the selected native menu path changed", code: .staleTarget) }
    }

    private func declares(_ action: String, on element: AXUIElement) -> Bool {
        var raw: CFArray?
        return AXUIElementCopyActionNames(element, &raw) == .success && (raw as? [String])?.contains(action) == true
    }

    private func branches() throws -> [Branch] {
        try validatePath()
        var branches = [Branch]()
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
        onCleanupUnknown: () -> Void, onNativePreparation: (NativePreparationEffect) -> Void
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
            if AXMessaging.performAction(menu, kAXCancelAction as CFString) != .success { accepted = false }
            onNativePreparation(.init(kind: .menuContext, phase: .returned, detail: "close prepared menu", window: nil))
        }
        await withCheckedContinuation { continuation in
            DispatchQueue.main.asyncAfter(deadline: .now() + .milliseconds(50)) { continuation.resume() }
        }
        if menus.contains(where: { NativeMenuSurfaces.isPresentedPopup($0, pid: application.processIdentifier) }) {
            accepted = false
        }
        if !accepted { onCleanupUnknown() }
        return accepted
    }

    private func refresh(
        _ branches: [Branch], check: () throws -> Void, onPreparation: (String) -> Void, onCleanupUnknown: () -> Void,
        onNativePreparation: (NativePreparationEffect) -> Void
    ) async throws {
        guard let process = NativeProcessIdentity.read(application.processIdentifier) else {
            throw DriverError("menu process disappeared", code: .staleTarget)
        }
        var opened = [AXUIElement]()
        do {
            for branch in branches {
                try check()
                try validatePath()
                try BackgroundTarget.validateReleasedInput()
                guard try receiverReady(), AXAccess.read(branch.opener, kAXEnabledAttribute).value as? Bool != false,
                    declares(kAXPressAction, on: branch.opener), declares(kAXCancelAction, on: branch.menu)
                else {
                    throw DriverError(
                        "the selected menu branch cannot be prepared in its current context", code: .inputUnavailable)
                }
                // Retain cleanup before calling: an AX error can still leave
                // menu tracking active. Never invoke the requested leaf here.
                opened.append(branch.menu)
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
                try await Task.sleep(for: .milliseconds(50))
            }
        } catch {
            _ = await close(
                opened, process: process, onPreparation: onPreparation, onCleanupUnknown: onCleanupUnknown,
                onNativePreparation: onNativePreparation)
            throw error
        }
        guard
            await close(
                opened, process: process, onPreparation: onPreparation, onCleanupUnknown: onCleanupUnknown,
                onNativePreparation: onNativePreparation)
        else { throw DriverError("menu preparation cleanup could not be confirmed", code: .executionUnknown) }
        try check()
        try validatePath()
    }

    func prepare(
        foreground: Bool, check: @escaping @MainActor () throws -> Void,
        onPreparation: @escaping @MainActor (String) -> Void, onCleanupUnknown: @escaping @MainActor () -> Void,
        onNativePreparation: @escaping @MainActor (NativePreparationEffect) -> Void = { _ in },
        onFocusOutcome: @escaping @MainActor (NativeFocusOutcome) -> Void = { _ in },
        onPreparationWait: @escaping @MainActor (NativePreparationWait) -> Void = { _ in }, observeOutcome: Bool = false
    ) async throws {
        try check()
        let branches = try branches()
        guard let window else { return }
        try BackgroundTarget.validateReleasedInput()
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
            guard let document = initial.document(for: window) else {
                throw NativePreparationUnavailable(
                    description: "the selected menu document context cannot be prepared in the background")
            }
            func set(_ object: AXUIElement, _ attribute: String, _ value: CFTypeRef) throws -> Bool {
                guard AXAccess.writable(object, attribute).value == true else { return false }
                try check()
                try BackgroundTarget.validateReleasedInput()
                onPreparation("menu context " + attribute)
                onNativePreparation(
                    .init(kind: .windowSelection, phase: .willSend, detail: "menu context " + attribute, window: nil))
                let status = AXMessaging.setAttributeValue(object, attribute as CFString, value)
                onNativePreparation(
                    .init(kind: .windowSelection, phase: .returned, detail: "menu context " + attribute, window: nil))
                if status == .invalidUIElement { throw DriverError("menu context window expired", code: .staleTarget) }
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
                try BackgroundTarget.validateReleasedInput()
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
        try await refresh(
            branches, check: check, onPreparation: onPreparation, onCleanupUnknown: onCleanupUnknown,
            onNativePreparation: onNativePreparation)
        if AXAccess.read(element, kAXEnabledAttribute).value as? Bool == false {
            throw DriverError("the menu command remains disabled in the selected document", code: .inputUnavailable)
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
            // A requested submenu needs its live ancestor menus. Opening a
            // detached item can leave AppKit in an invisible tracking loop.
            // These opens are part of the requested effect, not temporary
            // validation. Cross the input barrier before the first one and
            // never close the resulting menu path on normal completion.
            for branch in try branches() {
                try check()
                try validatePath()
                try BackgroundTarget.validateReleasedInput()
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
