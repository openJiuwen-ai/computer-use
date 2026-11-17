// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

@preconcurrency import ApplicationServices
import CoreGraphics
import Foundation

enum DesktopPermission: CaseIterable {
    case accessibility, screenCapture, postEvents

    // Permission attribution depends on the host. Track prompts only for this driver process.
    @MainActor private static var prompted = Set<Self>()

    var granted: Bool {
        switch self {
        case .accessibility: AXIsProcessTrusted()
        case .screenCapture: CGPreflightScreenCaptureAccess()
        case .postEvents: CGPreflightPostEventAccess()
        }
    }

    @MainActor func guideOnce() {
        guard !Self.prompted.contains(self) else { return }
        // Return the operation's existing result while macOS handles the request.
        DispatchQueue.main.async {
            guard !Self.prompted.contains(self), !granted else { return }
            Self.prompted.insert(self)
            request()
        }
    }

    @MainActor func request() {
        switch self {
        case .accessibility:
            let options = [kAXTrustedCheckOptionPrompt.takeUnretainedValue() as String: true] as CFDictionary
            _ = AXIsProcessTrustedWithOptions(options)
        case .screenCapture:
            _ = CGRequestScreenCaptureAccess()
        case .postEvents:
            _ = CGRequestPostEventAccess()
        }
    }
}
