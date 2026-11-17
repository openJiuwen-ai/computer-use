// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

@preconcurrency import ApplicationServices
import Foundation

// Compile alongside DesktopPermission.swift. These stand in for the OS
// permission calls so the test works without changing the host's settings.
nonisolated(unsafe) private var requests: [String] = []
nonisolated(unsafe) private var screenCaptureGranted = true

func AXIsProcessTrusted() -> Bool { false }
func CGPreflightScreenCaptureAccess() -> Bool { screenCaptureGranted }
func CGPreflightPostEventAccess() -> Bool { false }

func AXIsProcessTrustedWithOptions(_ options: CFDictionary?) -> Bool {
    let key = kAXTrustedCheckOptionPrompt.takeUnretainedValue() as String
    precondition((options as? [String: Bool])?[key] == true)
    requests.append("accessibility")
    return false
}

func CGRequestScreenCaptureAccess() -> Bool {
    requests.append("screenCapture")
    return false
}

func CGRequestPostEventAccess() -> Bool {
    requests.append("postEvents")
    return false
}

@main struct PermissionPromptTest {
    @MainActor static func main() async {
        DesktopPermission.accessibility.guideOnce()
        DesktopPermission.accessibility.guideOnce()
        DesktopPermission.postEvents.guideOnce()
        DesktopPermission.screenCapture.guideOnce()
        precondition(requests.isEmpty, "permission requests must be deferred")
        await drainMainQueue()
        precondition(requests == ["accessibility", "postEvents"], "first missing permissions should each prompt once")

        screenCaptureGranted = false
        DesktopPermission.screenCapture.guideOnce()
        DesktopPermission.screenCapture.guideOnce()
        await drainMainQueue()
        precondition(requests == ["accessibility", "postEvents", "screenCapture"], "a new permission gap should prompt")
    }

    private static func drainMainQueue() async {
        await withCheckedContinuation { continuation in
            DispatchQueue.main.async { continuation.resume() }
        }
    }
}
