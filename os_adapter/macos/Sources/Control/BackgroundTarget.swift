// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices

/// Registration is historical evidence for one observed application lifetime,
/// not a fresh input permission. Weak keys do not keep terminated app handles
/// alive; every use still re-reads and compares the kernel process identity.
@MainActor final class ApplicationRegistrationHistory {
    static let shared = ApplicationRegistrationHistory()
    private final class Proof {
        let identity: NativeProcessIdentity
        let launched: Date?
        init(_ identity: NativeProcessIdentity, launched: Date?) {
            self.identity = identity
            self.launched = launched
        }
    }
    private let proofs = NSMapTable<NSRunningApplication, Proof>.weakToStrongObjects()

    func confirmed(_ application: NSRunningApplication, identity: NativeProcessIdentity?, launched: Date?) -> Bool {
        guard let identity, let proof = proofs.object(forKey: application) else { return false }
        return proof.identity == identity && proof.launched == launched
    }

    func remember(_ application: NSRunningApplication, identity: NativeProcessIdentity, launched: Date?) {
        proofs.setObject(Proof(identity, launched: launched), forKey: application)
    }
}

/// A process identity from an observation, never a lookup of the current foreground app.
@MainActor struct BackgroundTarget {
    private static let desktopMode = ProcessInfo.processInfo.environment["COMPUTER_USE_DESKTOP"] ?? "shared"
    let application: NSRunningApplication
    let launched: Date?
    private let nativeIdentity: NativeProcessIdentity?
    private let registrationConfirmedAtCapture: Bool
    private let registrations: ApplicationRegistrationHistory
    private let owner: NSRunningApplication?
    private let ownerIdentity: NativeProcessIdentity?

    static var isExclusive: Bool { desktopMode == "exclusive" }
    var isForeground: Bool {
        NSWorkspace.shared.frontmostApplication?.processIdentifier == application.processIdentifier
    }
    var allowsMenuPopup: Bool { Self.isExclusive || isForeground }
    var capturedProcessIdentity: NativeProcessIdentity? { nativeIdentity }

    static func validateDesktop(input: Bool = false) throws {
        let foreground = NSWorkspace.shared.frontmostApplication
        guard foreground?.bundleIdentifier != "com.apple.loginwindow", !input || foreground != nil else {
            // Preserve the actual evidence behind a desktop refusal. A missing
            // foreground or loginwindow is not proof of a particular lock setting.
            let session = CGSessionCopyCurrentDictionary() as? [String: Any] ?? [:]
            func state(_ key: String) -> String { session[key].map { String(describing: $0) } ?? "unknown" }
            diagnose(
                """
                desktop unavailable: foreground=\(foreground?.bundleIdentifier ?? "none") \
                pid=\(foreground?.processIdentifier ?? 0), \
                displayAsleep=\(CGDisplayIsAsleep(CGMainDisplayID())), \
                loginDone=\(state("kCGSessionLoginDoneKey")), onConsole=\(state("kCGSessionOnConsoleKey")), \
                screenLocked=\(state("CGSSessionScreenIsLocked"))
                """)
            let reason = foreground == nil ? "foreground application unavailable" : "login window is in the foreground"
            throw DriverError(
                "desktop unavailable: \(reason)", code: .desktopUnavailable,
                next: "resume the desktop session before continuing")
        }
    }

    static func validateReleasedInput(allowing button: CGMouseButton? = nil) throws {
        let modifiers: CGEventFlags = [.maskCommand, .maskControl, .maskAlternate, .maskShift]
        guard CGEventSource.flagsState(.combinedSessionState).intersection(modifiers).isEmpty,
            ![CGMouseButton.left, .right, .center].contains(where: {
                $0 != button && CGEventSource.buttonState(.combinedSessionState, button: $0)
            })
        else {
            throw DriverError(
                "mouse buttons or modifiers are already held; release them before input", code: .inputUnavailable)
        }
    }

    enum MenuRecipient { case window(AXUIElement), application }

    func validateWindowlessMenu() throws {
        try validate(input: true)
        let root = AXUIElementCreateApplication(application.processIdentifier)
        guard let windows = AXAccess.elements(root, kAXWindowsAttribute, limit: 0), windows.isEmpty,
            let native = NativeWindows.listIfAvailable(application), native.allSatisfy({ $0.menu != nil })
        else {
            throw DriverError(
                "the observed windowless application now has a window, or its window catalogue is unavailable",
                code: .staleTarget, next: "read the app and choose the new window before using its menu")
        }
        for key in [kAXMainWindowAttribute, kAXFocusedWindowAttribute] {
            switch AXAccess.read(root, key) {
            case .unsupported, .unavailable(.noValue): break
            default:
                throw DriverError(
                    "windowless menu receiving scope is no longer confirmed", code: .staleTarget,
                    next: "read the app to inspect its receiving window")
            }
        }
    }

    init(
        _ application: NSRunningApplication, owner: NSRunningApplication? = nil,
        registrations: ApplicationRegistrationHistory = .shared,
        applicationForPID: (pid_t) -> NSRunningApplication? = { NSRunningApplication(processIdentifier: $0) }
    ) {
        self.application = application
        self.launched = application.launchDate
        self.nativeIdentity = NativeProcessIdentity.read(application.processIdentifier)
        self.registrations = registrations
        let registered = applicationForPID(application.processIdentifier)
        let matched = registered.map { $0.isEqual(application) && $0.launchDate == application.launchDate } ?? false
        if matched, let nativeIdentity, !application.isTerminated {
            registrations.remember(application, identity: nativeIdentity, launched: launched)
        }
        self.registrationConfirmedAtCapture =
            matched || registrations.confirmed(application, identity: nativeIdentity, launched: launched)
        self.owner = owner
        self.ownerIdentity = owner.flatMap { NativeProcessIdentity.read($0.processIdentifier) }
    }

    func validate(
        input: Bool = false,
        applicationForPID: (pid_t) -> NSRunningApplication? = { NSRunningApplication(processIdentifier: $0) }
    ) throws { _ = try validate(input: input, requireRegistration: true, applicationForPID: applicationForPID) }

    /// Startup can precede AppKit registration. False means wait, never permission
    /// to observe or send input. Every poll still proves the exact kernel lifetime;
    /// exit, replacement and conflicting registration remain immediate failures.
    func launchRegistrationReady(
        applicationForPID: (pid_t) -> NSRunningApplication? = { NSRunningApplication(processIdentifier: $0) }
    ) throws -> Bool { try validate(input: false, requireRegistration: false, applicationForPID: applicationForPID) }

    private func validate(input: Bool, requireRegistration: Bool, applicationForPID: (pid_t) -> NSRunningApplication?)
        throws -> Bool
    {
        // Cancellation stops further input, but identity-only checks used by
        // key/button release must remain available while the task unwinds.
        if input { try RequestDiagnostics.checkCancellation() }
        guard ["shared", "exclusive"].contains(Self.desktopMode) else {
            throw DriverError("COMPUTER_USE_DESKTOP must be shared or exclusive", code: .modeUnsupported)
        }
        // Preserve the original short-circuit order. Construct diagnostic fields
        // only on failure, from the exact values this validation evaluated.
        // In particular, a later lookup must not replace missing process data.
        let terminated = application.isTerminated
        let registrationConfirmed =
            registrationConfirmedAtCapture
            || registrations.confirmed(application, identity: nativeIdentity, launched: launched)
        func identityFailure(
            _ check: String, currentPresent: Bool? = nil, equal: Bool? = nil, launchRead: Bool = false,
            actualLaunch: Date? = nil, nativeRead: Bool = false, actualNative: NativeProcessIdentity? = nil,
            readFailure: NativeProcessIdentity.ReadFailure? = nil
        ) -> DriverError {
            func native(_ identity: NativeProcessIdentity?) -> Any {
                guard let identity else { return NSNull() }
                return [
                    "pid": identity.pid, "startedSeconds": identity.seconds,
                    "startedMicroseconds": identity.microseconds, "executable": identity.executable
                ] as [String: Any]
            }
            func date(_ value: Date?) -> Any { value.map { $0.timeIntervalSince1970 as Any } ?? NSNull() }
            var values: [String: Any] = [
                "kind": "process_identity_validation_failed", "failedCheck": check,
                "pid": application.processIdentifier, "isTerminated": terminated, "expectedLaunch": date(launched),
                "expectedNative": native(nativeIdentity),
                "registrationConfirmedAtCapture": registrationConfirmedAtCapture,
                "registrationConfirmed": registrationConfirmed, "ownerPresent": owner != nil,
                "currentPresent": currentPresent.map { $0 as Any } ?? "not evaluated",
                "applicationEqual": equal.map { $0 as Any } ?? "not evaluated",
                "actualLaunch": launchRead ? date(actualLaunch) : "not evaluated",
                "actualNative": nativeRead ? native(actualNative) : "not evaluated"
            ]
            if let readFailure { values["nativeReadFailure"] = readFailure.diagnostic }
            diagnose(values)
            let detail =
                check == "currentApplication"
                ? "application registration is unavailable and has not been confirmed for this process lifetime"
                : readFailure != nil
                    ? "target process identity could not be read; read the app again"
                    : "target process exited or changed; read the app again"
            return DriverError(detail, code: .staleTarget)
        }
        guard !terminated else { throw identityFailure("isTerminated") }
        // AppKit's application registry is metadata, not a complete process
        // table. A missing entry must not invalidate an unchanged kernel birth
        // identity. If we could not capture that identity, retain the AppKit
        // lifetime requirement; a PID alone never authorizes a target.
        let current = applicationForPID(application.processIdentifier)
        let equal = current.map { $0.isEqual(application) }
        let actualLaunch = current?.launchDate
        if current != nil {
            guard equal == true else {
                throw identityFailure("applicationEquality", currentPresent: true, equal: equal)
            }
            guard actualLaunch == launched else {
                throw identityFailure(
                    "launchDate", currentPresent: true, equal: equal, launchRead: true, actualLaunch: actualLaunch)
            }
        } else if nativeIdentity == nil || (requireRegistration && !registrationConfirmed) {
            throw identityFailure("currentApplication", currentPresent: false)
        }
        if let expected = nativeIdentity {
            var readFailure: NativeProcessIdentity.ReadFailure?
            let actualNative = NativeProcessIdentity.read(
                application.processIdentifier, onFailure: { readFailure = $0 })
            guard actualNative == expected else {
                throw identityFailure(
                    "nativeIdentity", currentPresent: current != nil, equal: equal, launchRead: current != nil,
                    actualLaunch: actualLaunch, nativeRead: true, actualNative: actualNative, readFailure: readFailure)
            }
            if current != nil {
                registrations.remember(application, identity: expected, launched: launched)
            } else if registrationConfirmed {
                diagnose(
                    [
                        "kind": "process_identity_kernel_confirmed", "pid": expected.pid,
                        "startedSeconds": expected.seconds, "startedMicroseconds": expected.microseconds,
                        "reason":
                            "AppKit application registration unavailable; exact observed kernel identity unchanged"
                    ] as [String: Any])
            }
        } else if owner != nil {
            throw identityFailure(
                "missingOwnedProcessIdentity", currentPresent: current != nil, equal: equal, launchRead: current != nil,
                actualLaunch: actualLaunch)
        }
        if let owner {
            guard !owner.isTerminated, let ownerIdentity,
                NativeProcessIdentity.read(owner.processIdentifier) == ownerIdentity, let nativeIdentity,
                NativeWindows.belongs(nativeIdentity, to: owner)
            else {
                throw DriverError(
                    "auxiliary window no longer belongs to the observed app; read the app again", code: .staleTarget)
            }
        }
        if input { try Self.validateDesktop(input: true) }
        return current != nil || registrationConfirmed
    }
}
