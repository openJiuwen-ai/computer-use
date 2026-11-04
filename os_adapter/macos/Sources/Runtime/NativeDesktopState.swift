// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices

/// Native resources for one protocol 6 session. There is no second observation
/// cache or remembered keyboard recipient; operations own their input evidence.
@MainActor final class NativeDesktopState {
    private(set) var effectPossible = false
    let discovery = ApplicationDiscovery()
    let appSelections: AppSelections
    let applications: NativeApplicationAccess
    let sharing = WindowSharing()
    let pointer = AgentPointer()
    private(set) lazy var desktop = DesktopCapture(applications: discovery)
    let accessibilityActivation = AccessibilityActivation()

    init(applications: NativeApplicationAccess = NativeApplicationAccess(), appSelections: AppSelections = AppSelections()) {
        self.applications = applications
        self.appSelections = appSelections
    }

    func beginRequest() { effectPossible = false }
    func recordEffect() { effectPossible = true }
}
