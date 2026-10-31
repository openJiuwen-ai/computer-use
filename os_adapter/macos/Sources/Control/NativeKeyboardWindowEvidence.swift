// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

/// Prefer authorized foreground setup before synthetic document activation only
/// when declared AX selectors have no remaining selection to perform. This is
/// a preparation choice, not a universal key-window or dispatch prerequisite.
/// Element-addressed shortcuts still belong to their confirmed window scope.
enum NativeKeyboardWindowEvidence {
    struct BackgroundSelection {
        var windowScope: Bool
        var windowCommand: Bool
        var foreground: Bool
        var standardWindow: Bool
        var focused: Bool?
        var windowFocusWritable: Bool?
        var appFocusWritable: Bool?
        var main: Bool?
        var mainWindowMatches: Bool?
        var focusedWindowMatches: Bool?
    }

    /// Missing properties and writable selectors retain the background route. AXFocused
    /// false alone does not prove an application cannot consume a shortcut.
    static func prefersForegroundBeforeSynthetic(_ facts: BackgroundSelection) -> Bool {
        facts.windowScope && facts.windowCommand && !facts.foreground && facts.standardWindow
            && facts.focused == false && facts.windowFocusWritable == false && facts.appFocusWritable == false
            && facts.main == true && facts.mainWindowMatches == true && facts.focusedWindowMatches == true
    }

}
