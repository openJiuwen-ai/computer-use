// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

@preconcurrency import ApplicationServices
import CoreGraphics
import Foundation

enum DesktopPermission: String {
    case accessibility, screenCapture, postEvents

    var granted: Bool {
        switch self {
        case .accessibility: AXIsProcessTrusted()
        case .screenCapture: CGPreflightScreenCaptureAccess()
        case .postEvents: CGPreflightPostEventAccess()
        }
    }

    var settingsHelp: String {
        let setting = self == .screenCapture ? "Screen Recording" : "Accessibility"
        return "Enable Jiuwen CUA in System Settings > Privacy & Security > \(setting)."
    }

    @MainActor func guideOnce() {
        // The guide belongs to the Launch Services instance requesting access.
        guard CommandLine.arguments.contains("--connect-stdio") else { return }
        let key = "didRequestDesktopPermission.\(rawValue)"
        guard !UserDefaults.standard.bool(forKey: key) else { return }
        // Let the affected operation return its permission gap. The system
        // owns the approval UI and the user can retry after granting access.
        DispatchQueue.main.async {
            guard !granted, !UserDefaults.standard.bool(forKey: key) else { return }
            request()
            UserDefaults.standard.set(true, forKey: key)
        }
    }

    private func request() {
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

    static func status(request: Bool) -> [String: Any] {
        if request {
            if !Self.accessibility.granted {
                Self.accessibility.request()
            } else if !Self.postEvents.granted {
                Self.postEvents.request()
            }
            if !Self.screenCapture.granted { Self.screenCapture.request() }
        }
        return [
            "accessibility": Self.accessibility.granted,
            "screenCapture": Self.screenCapture.granted,
            "input": Self.postEvents.granted,
            "executable": Bundle.main.executablePath ?? CommandLine.arguments[0]
        ]
    }
}
