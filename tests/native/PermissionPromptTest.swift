// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import Foundation

// Compile alongside DesktopPermission.swift. These stand in for the OS
// permission calls so the test works without changing the host's settings.
nonisolated(unsafe) private var screenCaptureGranted = true

func AXIsProcessTrusted() -> Bool { false }
func CGPreflightScreenCaptureAccess() -> Bool { screenCaptureGranted }
func CGPreflightPostEventAccess() -> Bool { false }

@MainActor enum PermissionGuide {
    static private(set) var opened = [DesktopPermission]()

    static func open(startingAt permission: DesktopPermission) {
        opened.append(permission)
    }
}

@main struct PermissionPromptTest {
    @MainActor static func main() async {
        DesktopPermission.screenCapture.guideOnce()
        await drainMainQueue()
        precondition(PermissionGuide.opened.isEmpty, "granted access should leave the desktop alone")

        screenCaptureGranted = false
        let first: DesktopPermission = CommandLine.arguments.contains("--accessibility") ? .postEvents : .screenCapture
        first.guideOnce()
        first.guideOnce()
        DesktopPermission.screenCapture.guideOnce()
        DesktopPermission.accessibility.guideOnce()
        precondition(PermissionGuide.opened.isEmpty, "opening the guide must be deferred")
        await drainMainQueue()
        precondition(PermissionGuide.opened == [first], "show one guide starting at the first missing permission")
    }

    private static func drainMainQueue() async {
        await withCheckedContinuation { continuation in
            DispatchQueue.main.async { continuation.resume() }
        }
    }
}
