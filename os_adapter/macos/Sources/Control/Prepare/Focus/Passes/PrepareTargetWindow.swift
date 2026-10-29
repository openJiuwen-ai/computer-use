// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices

/// PREPARE: select a standard document; menus and non-key panels keep their context.
@MainActor enum PrepareTargetWindowPass {
    static func run(_ request: NativeFocusRequest) throws {
        guard let window = request.element,
            AXAccess.read(window, kAXSubroleAttribute).value as? String == kAXStandardWindowSubrole
        else { return }
        for attribute in [kAXMainAttribute, kAXFocusedAttribute] {
            guard AXAccess.writable(window, attribute).value == true else { continue }
            try request.check()
            try request.target.validate(input: true)
            request.onPreparation("native window selection")
            request.onNativePreparation(
                .init(
                    kind: .windowSelection, phase: .willSend, detail: "native window selection \(attribute)",
                    window: request.window.id))
            _ = AXMessaging.setAttributeValue(window, attribute as CFString, kCFBooleanTrue)
            request.onNativePreparation(
                .init(
                    kind: .windowSelection, phase: .returned, detail: "native window selection \(attribute)",
                    window: request.window.id))
        }
    }
}
