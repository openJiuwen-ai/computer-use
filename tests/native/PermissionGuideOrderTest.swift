// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

// Compile with DesktopPermission.swift and PermissionGuide.swift.
func diagnose(_ message: String) {}

@main struct PermissionGuideOrderTest {
    static func main() {
        var order = PermissionGuideOrder()
        precondition(order.needsPlacement(guideIndex: nil, settingsIndex: 1), "attach the guide when it first appears")
        precondition(!order.needsPlacement(guideIndex: nil, settingsIndex: 1), "polling must not repeatedly order the guide")
        order.refresh()
        precondition(!order.needsPlacement(guideIndex: 0, settingsIndex: 2), "a panel already above Settings can stay put")
        order.refresh()
        precondition(order.needsPlacement(guideIndex: 2, settingsIndex: 1), "restore the guide after Settings is raised")
        precondition(!order.needsPlacement(guideIndex: 2, settingsIndex: 1), "request a single repair while Settings settles")
    }
}
