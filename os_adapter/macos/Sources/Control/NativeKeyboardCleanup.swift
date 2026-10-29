// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import CoreGraphics

/// A captured HID key-up clears session state without repeating request text.
/// The virtual key can still be observed by another App's key-up handler.
enum NativeKeyboardCleanup {
    static func scopedUp(_ event: CGEvent) {
        // A nil pointer is a no-op on the tested OS even when length is zero.
        // A valid zero-length buffer explicitly removes the existing payload.
        // Flags must be cleared before the Unicode field: changing flags can
        // rebuild keyboard text from the virtual key after it was cleared.
        event.flags = []
        var empty: UniChar = 0
        event.keyboardSetUnicodeString(stringLength: 0, unicodeString: &empty)
    }
}
