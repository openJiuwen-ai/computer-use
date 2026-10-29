// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

/// Prefer authorized foreground setup before synthetic document activation only
/// when declared AX selectors have no remaining selection to perform. This is
/// a preparation choice, not a universal key-window or dispatch prerequisite.
enum NativeKeyboardWindowEvidence {
    struct BackgroundSelection {
        var foregroundAllowed: Bool
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

    /// Strict shared input keeps the existing background attempt. Missing
    /// properties and writable selectors also retain that route. AXFocused
    /// false alone does not prove an application cannot consume a shortcut.
    static func prefersForegroundBeforeSynthetic(_ facts: BackgroundSelection) -> Bool {
        facts.foregroundAllowed && facts.windowScope && facts.windowCommand && !facts.foreground && facts.standardWindow
            && facts.focused == false && facts.windowFocusWritable == false && facts.appFocusWritable == false
            && facts.main == true && facts.mainWindowMatches == true && facts.focusedWindowMatches == true
    }

}
