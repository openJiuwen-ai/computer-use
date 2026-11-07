// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices

struct NativePrimitiveContract {
    let id: String
    let node: String?
    let within: [String]
    let actuator: NativeInspectQuery.Actuator?
    let receiver: NativeInspectQuery.Receiver?
    let basis: [NativeInspectQuery.Dependency]
    let unit: NativeInputUnit
    var click: NativeClickContract? = nil
    /// Once another document is known, a later unavailable focus read cannot
    /// erase the original window's responder-context requirement.
    var actionDocument: NativeActionDocumentContract? = nil
    /// A keyboard-only insertion recipe on this contract's exact node.
    var keyboardInsertion: NativeKeyboardTextEntry? = nil
    var geckoKeyboardInsertion: NativeGeckoTextProvider.Proof? = nil
}
