// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import ApplicationServices
import CoreGraphics
import Foundation

enum DesktopPermission: String, CaseIterable {
    case accessibility, screenCapture, postEvents

    @MainActor private static var guideStarted = false

    var granted: Bool {
        switch self {
        case .accessibility: AXIsProcessTrusted()
        case .screenCapture: CGPreflightScreenCaptureAccess()
        case .postEvents: CGPreflightPostEventAccess()
        }
    }

    @MainActor func guideOnce() {
        guard !Self.guideStarted else { return }
        DispatchQueue.main.async {
            guard !Self.guideStarted, !granted else { return }
            Self.guideStarted = true
            PermissionGuide.open(startingAt: self)
        }
    }
}
